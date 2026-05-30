import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import FastAPI, Depends, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from config import settings
from database import get_db, engine
from models import Base, User, Campaign, Link, Pixel, Event, Click, Postback, Stat, Rule, RuleLog, Setting, AdAccount
from auth import hash_password, verify_password, create_access_token, create_refresh_token, get_current_user, require_role
from geoip import get_country, get_device_info
from utils import generate_short_code, generate_qr_png, GIF_1X1, apply_macros, add_utm_params
from rtb_bridge.router import router as rtb_router

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# App init
# ─────────────────────────────────────────
app = FastAPI(
    title="Central DSP API",
    version="3.0.0",
    description="Self-hosted DSP: SmartLink + Tracker + AutoPilot + RTB",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rtb_router)


# ─────────────────────────────────────────
# Startup: create tables + first admin
# ─────────────────────────────────────────
@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    db = next(get_db())
    try:
        if db.query(User).count() == 0:
            admin = User(
                email=settings.FIRST_ADMIN_EMAIL,
                password_hash=hash_password(settings.FIRST_ADMIN_PASSWORD),
                full_name="Admin",
                role="admin",
                is_active=True,
            )
            db.add(admin)
            db.commit()
            logger.warning(
                f"[STARTUP] First admin created: {settings.FIRST_ADMIN_EMAIL} "
                f"/ {settings.FIRST_ADMIN_PASSWORD} — CHANGE PASSWORD!"
            )
    finally:
        db.close()


# ─────────────────────────────────────────
# PYDANTIC SCHEMAS
# ─────────────────────────────────────────
class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class UserOut(BaseModel):
    id: int
    email: str
    full_name: Optional[str]
    role: str
    is_active: bool
    class Config:
        from_attributes = True

class UserCreate(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = None
    role: str = "viewer"

class CampaignCreate(BaseModel):
    name: str
    offer_value: float = 0.0
    attribution_model: str = "last_click"
    daily_budget: Optional[float] = None
    total_budget: Optional[float] = None
    is_ab: bool = False

class CampaignOut(BaseModel):
    id: int
    name: str
    status: str
    camp_code: str
    offer_value: float
    attribution_model: str
    is_ab: bool
    created_at: datetime
    class Config:
        from_attributes = True

class LinkCreate(BaseModel):
    platform: Optional[str] = None
    external_id: Optional[str] = None
    original_url: str
    custom_slug: Optional[str] = None
    cost_per_click: float = 0.0
    expires_in_days: Optional[int] = None
    geo_rules: dict = {}
    device_rules: dict = {}

class LinkOut(BaseModel):
    id: int
    short_code: str
    custom_slug: Optional[str]
    original_url: str
    platform: Optional[str]
    is_active: bool
    cost_per_click: float
    created_at: datetime
    class Config:
        from_attributes = True

class PixelCreate(BaseModel):
    name: str

class PixelOut(BaseModel):
    id: int
    name: str
    pixel_id: str
    is_active: bool
    last_fire_at: Optional[datetime]
    total_events: int
    created_at: datetime
    class Config:
        from_attributes = True

class RuleCreate(BaseModel):
    level: str = "campaign"
    metric: str
    condition: str
    threshold: float
    action: str

class RuleOut(BaseModel):
    id: int
    level: str
    metric: str
    condition: str
    threshold: float
    action: str
    triggered: bool
    is_active: bool
    class Config:
        from_attributes = True

class StatsOut(BaseModel):
    clicks: int
    conversions: int
    bounces: int
    cost: float
    revenue: float
    roi: float
    cr: float
    epc: float


# ─────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────
@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "version": "3.0.0"}


# ─────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────
@app.post("/auth/login", response_model=TokenOut, tags=["Auth"])
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username, User.is_active == True).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    access = create_access_token({"sub": str(user.id)})
    refresh = create_refresh_token({"sub": str(user.id)})
    return TokenOut(access_token=access, refresh_token=refresh)


@app.get("/auth/me", response_model=UserOut, tags=["Auth"])
def me(current_user: User = Depends(get_current_user)):
    return current_user


@app.post("/auth/refresh", response_model=TokenOut, tags=["Auth"])
def refresh_token(request: Request, db: Session = Depends(get_db)):
    from auth import decode_token
    body_token = request.headers.get("X-Refresh-Token")
    if not body_token:
        raise HTTPException(status_code=400, detail="X-Refresh-Token header required")
    payload = decode_token(body_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Not a refresh token")
    user = db.query(User).filter(User.id == int(payload["sub"]), User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return TokenOut(
        access_token=create_access_token({"sub": str(user.id)}),
        refresh_token=create_refresh_token({"sub": str(user.id)}),
    )


# ─────────────────────────────────────────
# USERS (admin only)
# ─────────────────────────────────────────
@app.get("/api/users", response_model=List[UserOut], tags=["Users"])
def list_users(admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    return db.query(User).order_by(User.id).all()


@app.post("/api/users", response_model=UserOut, status_code=201, tags=["Users"])
def create_user(body: UserCreate, admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=409, detail="Email already exists")
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.delete("/api/users/{user_id}", tags=["Users"])
def delete_user(user_id: int, admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"ok": True}


# ─────────────────────────────────────────
# CAMPAIGNS
# ─────────────────────────────────────────
@app.get("/api/campaigns", response_model=List[CampaignOut], tags=["Campaigns"])
def list_campaigns(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Campaign)
    if current_user.role != "admin":
        q = q.filter(Campaign.user_id == current_user.id)
    return q.order_by(Campaign.created_at.desc()).all()


@app.post("/api/campaigns", response_model=CampaignOut, status_code=201, tags=["Campaigns"])
def create_campaign(body: CampaignCreate, current_user: User = Depends(require_role("admin", "manager")), db: Session = Depends(get_db)):
    camp = Campaign(
        user_id=current_user.id,
        name=body.name,
        camp_code=generate_short_code(12),
        offer_value=body.offer_value,
        attribution_model=body.attribution_model,
        daily_budget=body.daily_budget,
        total_budget=body.total_budget,
        is_ab=body.is_ab,
    )
    db.add(camp)
    db.commit()
    db.refresh(camp)
    return camp


@app.get("/api/campaigns/{campaign_id}", response_model=CampaignOut, tags=["Campaigns"])
def get_campaign(campaign_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    camp = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if current_user.role != "admin" and camp.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return camp


@app.patch("/api/campaigns/{campaign_id}/status", tags=["Campaigns"])
def update_campaign_status(campaign_id: int, status_val: str, current_user: User = Depends(require_role("admin", "manager")), db: Session = Depends(get_db)):
    camp = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if status_val not in ("active", "stopped", "paused", "archived"):
        raise HTTPException(status_code=400, detail="Invalid status")
    camp.status = status_val
    db.commit()
    return {"ok": True, "status": status_val}


@app.delete("/api/campaigns/{campaign_id}", tags=["Campaigns"])
def delete_campaign(campaign_id: int, current_user: User = Depends(require_role("admin", "manager")), db: Session = Depends(get_db)):
    camp = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    db.delete(camp)
    db.commit()
    return {"ok": True}


# ─────────────────────────────────────────
# LINKS
# ─────────────────────────────────────────
@app.get("/api/campaigns/{campaign_id}/links", response_model=List[LinkOut], tags=["Links"])
def list_links(campaign_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Link).filter(Link.campaign_id == campaign_id).all()


@app.post("/api/campaigns/{campaign_id}/links", response_model=LinkOut, status_code=201, tags=["Links"])
def create_link(campaign_id: int, body: LinkCreate, current_user: User = Depends(require_role("admin", "manager")), db: Session = Depends(get_db)):
    camp = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if body.custom_slug and db.query(Link).filter(Link.custom_slug == body.custom_slug).first():
        raise HTTPException(status_code=409, detail="Slug already taken")
    expires_at = None
    if body.expires_in_days:
        from datetime import timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
    link = Link(
        campaign_id=campaign_id,
        platform=body.platform,
        external_id=body.external_id,
        original_url=body.original_url,
        short_code=generate_short_code(),
        custom_slug=body.custom_slug,
        cost_per_click=body.cost_per_click,
        expires_at=expires_at,
        geo_rules=body.geo_rules,
        device_rules=body.device_rules,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


@app.get("/api/links/{code}/qr", tags=["Links"])
def get_qr(code: str, request: Request):
    base_url = str(request.base_url).rstrip("/")
    url = f"{base_url}/s/{code}"
    return Response(content=generate_qr_png(url), media_type="image/png")


# ─────────────────────────────────────────
# TRACKING — PUBLIC (no auth required!)
# ─────────────────────────────────────────
def _resolve_link(code: str, db: Session) -> Link:
    link = db.query(Link).filter(
        (Link.short_code == code) | (Link.custom_slug == code),
        Link.is_active == True,
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    if link.expires_at and link.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Link expired")
    return link


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


@app.get("/s/{code}", tags=["Tracking"])
def redirect_link(
    code: str,
    request: Request,
    sub_id: str = "unknown",
    creative_id: str = "unknown",
    db: Session = Depends(get_db),
):
    link = _resolve_link(code, db)
    ip = _get_client_ip(request)
    ua_str = request.headers.get("User-Agent", "")
    country = get_country(ip)
    device_type, os_name, browser_name = get_device_info(ua_str)

    # Choose target URL from geo/device rules
    target_url = link.original_url
    geo_rules: dict = link.geo_rules or {}
    device_rules: dict = link.device_rules or {}

    if device_rules:
        target_url = device_rules.get(device_type) or device_rules.get("default") or target_url
    if geo_rules and country:
        target_url = geo_rules.get(country) or geo_rules.get("default") or target_url

    # Generate click_id
    click_uuid = uuid.uuid4()

    # Macro substitution in URL
    target_url = apply_macros(target_url, {
        "sub_id": sub_id,
        "creative_id": creative_id,
        "click_id": str(click_uuid),
        "country": country or "",
        "device": device_type,
        "platform": link.platform or "",
        "timestamp": str(int(datetime.now(timezone.utc).timestamp())),
    })

    # Record click
    click = Click(
        link_id=link.id,
        sub_id=sub_id,
        creative_id=creative_id,
        click_id=click_uuid,
        ip=ip,
        country_code=country,
        device_type=device_type,
        cost=link.cost_per_click or 0,
    )
    db.add(click)

    # Update legacy stats
    stat = db.query(Stat).filter(
        Stat.link_id == link.id,
        Stat.sub_id == sub_id,
        Stat.creative_id == creative_id,
    ).first()
    if stat:
        stat.clicks += 1
        stat.cost = (stat.cost or 0) + (link.cost_per_click or 0)
    else:
        stat = Stat(
            link_id=link.id,
            sub_id=sub_id,
            creative_id=creative_id,
            clicks=1,
            cost=link.cost_per_click or 0,
        )
        db.add(stat)
    db.commit()

    # A/B rotation for campaign
    if link.campaign.is_ab:
        links = db.query(Link).filter(
            Link.campaign_id == link.campaign_id,
            Link.is_active == True,
        ).all()
        if links:
            import random
            link = random.choice(links)
            target_url = link.original_url

    return RedirectResponse(url=target_url, status_code=302)


@app.get("/c/{camp_code}", tags=["Tracking"])
def redirect_ab(
    camp_code: str,
    request: Request,
    sub_id: str = "unknown",
    creative_id: str = "unknown",
    db: Session = Depends(get_db),
):
    """A/B rotator — randomly selects a link from campaign."""
    import random
    camp = db.query(Campaign).filter(Campaign.camp_code == camp_code, Campaign.status == "active").first()
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    links = db.query(Link).filter(Link.campaign_id == camp.id, Link.is_active == True).all()
    if not links:
        raise HTTPException(status_code=404, detail="No active links in campaign")
    link = random.choice(links)
    return RedirectResponse(url=f"/s/{link.short_code}?sub_id={sub_id}&creative_id={creative_id}", status_code=302)


@app.get("/track/{code}", tags=["Tracking"])
def track_event(
    code: str,
    request: Request,
    event: str = "conversion",
    val: Optional[float] = None,
    db: Session = Depends(get_db),
):
    """Legacy tracking pixel endpoint — backward compat with v2."""
    link = _resolve_link(code, db)
    if event == "conversion":
        stat = db.query(Stat).filter(Stat.link_id == link.id).first()
        if stat:
            stat.conversions += 1
            revenue = float(link.campaign.offer_value or 0)
            stat.revenue = (stat.revenue or 0) + revenue
            db.commit()
    elif event == "engage":
        stat = db.query(Stat).filter(Stat.link_id == link.id).first()
        if stat:
            stat.bounces = max(0, (stat.bounces or 1) - 1)
            db.commit()
    elif event == "time" and val:
        stat = db.query(Stat).filter(Stat.link_id == link.id).first()
        if stat:
            stat.total_time = (stat.total_time or 0) + int(val)
            db.commit()
    return Response(content=GIF_1X1, media_type="image/gif",
                    headers={"Cache-Control": "no-cache, no-store"})


# ─────────────────────────────────────────
# PIXEL CENTER
# ─────────────────────────────────────────
@app.get("/api/pixels/{campaign_id}", response_model=List[PixelOut], tags=["Pixels"])
def list_pixels(campaign_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Pixel).filter(Pixel.campaign_id == campaign_id).all()


@app.post("/api/pixels/{campaign_id}", response_model=PixelOut, status_code=201, tags=["Pixels"])
def create_pixel(campaign_id: int, body: PixelCreate, current_user: User = Depends(require_role("admin", "manager")), db: Session = Depends(get_db)):
    camp = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    pixel = Pixel(campaign_id=campaign_id, name=body.name)
    db.add(pixel)
    db.commit()
    db.refresh(pixel)
    return pixel


@app.delete("/api/pixels/{pixel_id}", tags=["Pixels"])
def delete_pixel(pixel_id: str, current_user: User = Depends(require_role("admin", "manager")), db: Session = Depends(get_db)):
    pixel = db.query(Pixel).filter(Pixel.pixel_id == pixel_id).first()
    if not pixel:
        raise HTTPException(status_code=404, detail="Pixel not found")
    db.delete(pixel)
    db.commit()
    return {"ok": True}


@app.get("/px/{pixel_id}.js", tags=["Tracking"])
def serve_pixel_js(pixel_id: str, request: Request, db: Session = Depends(get_db)):
    """Serve generated JS pixel for embedding on landing pages."""
    pixel = db.query(Pixel).filter(Pixel.pixel_id == pixel_id, Pixel.is_active == True).first()
    if not pixel:
        raise HTTPException(status_code=404, detail="Pixel not found")
    base_url = str(request.base_url).rstrip("/")
    js_code = f"""(function(w,d,pid){{
  w._dsp=w._dsp||[];
  w.dspEvent=function(name,data){{
    var p=new URLSearchParams(w.location.search);
    var sid=_dsp_sid();
    fetch('{base_url}/px/'+pid+'/event',{{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{
        event_name:name||'pageview',
        sub_id:p.get('sub_id')||'unknown',
        creative_id:p.get('creative_id')||'unknown',
        click_id:p.get('click_id')||sid,
        value:(data&&data.value)||0,
        currency:(data&&data.currency)||'USD',
        metadata:data||{{}},
        referer:d.referrer,
        session_id:sid
      }})
    }}).catch(function(){{}});
  }};
  w.dspEvent('pageview');
  function _engage(){{
    w.dspEvent('engage');
    d.removeEventListener('scroll',_engage);
    d.removeEventListener('click',_engage);
  }}
  d.addEventListener('scroll',_engage);
  d.addEventListener('click',_engage);
  var _t=0;
  setInterval(function(){{_t+=10;w.dspEvent('time_on_site',{{value:_t}});}},10000);
  function _dsp_sid(){{
    var k='_dsp_sid';
    try{{
      var s=localStorage.getItem(k);
      if(!s){{s=Math.random().toString(36).substr(2,9)+Date.now().toString(36);localStorage.setItem(k,s);}}
      return s;
    }}catch(e){{return Math.random().toString(36).substr(2,9);}}
  }}
}})(window,document,'{pixel_id}');"""
    return Response(content=js_code, media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})


@app.post("/px/{pixel_id}/event", tags=["Tracking"])
async def receive_pixel_event(pixel_id: str, request: Request, db: Session = Depends(get_db)):
    """Accept JS pixel events (pageview, engage, lead, purchase, etc.)"""
    pixel = db.query(Pixel).filter(Pixel.pixel_id == pixel_id, Pixel.is_active == True).first()
    if not pixel:
        return Response(status_code=204)
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=204)
    ip = _get_client_ip(request)
    ua_str = request.headers.get("User-Agent", "")
    country = get_country(ip)
    device_type, os_name, browser_name = get_device_info(ua_str)
    event = Event(
        pixel_id=pixel.pixel_id,
        session_id=body.get("session_id"),
        event_name=body.get("event_name", "pageview"),
        sub_id=body.get("sub_id"),
        creative_id=body.get("creative_id"),
        click_id=body.get("click_id"),
        value=float(body.get("value") or 0),
        currency=body.get("currency", "USD"),
        metadata_=body.get("metadata", {}),
        ip=ip,
        country_code=country,
        device_type=device_type,
        os=os_name,
        browser=browser_name,
        user_agent=ua_str,
        referer=body.get("referer"),
    )
    db.add(event)
    pixel.total_events = (pixel.total_events or 0) + 1
    pixel.last_fire_at = datetime.now(timezone.utc)
    db.add(pixel)
    db.commit()
    return Response(status_code=204)


@app.get("/px/{pixel_id}/1x1.gif", tags=["Tracking"])
def impression_pixel(pixel_id: str, request: Request, db: Session = Depends(get_db),
                     sub_id: str = "unknown", creative_id: str = "unknown"):
    """1x1 impression pixel for banners/push notifications."""
    pixel = db.query(Pixel).filter(Pixel.pixel_id == pixel_id, Pixel.is_active == True).first()
    if pixel:
        ip = _get_client_ip(request)
        country = get_country(ip)
        device_type, _, _ = get_device_info(request.headers.get("User-Agent", ""))
        event = Event(
            pixel_id=pixel.pixel_id,
            event_name="impression",
            sub_id=sub_id,
            creative_id=creative_id,
            ip=ip,
            country_code=country,
            device_type=device_type,
        )
        db.add(event)
        pixel.total_events = (pixel.total_events or 0) + 1
        pixel.last_fire_at = datetime.now(timezone.utc)
        db.add(pixel)
        db.commit()
    return Response(content=GIF_1X1, media_type="image/gif",
                    headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


# ─────────────────────────────────────────
# S2S POSTBACK
# ─────────────────────────────────────────
@app.get("/postback", tags=["Tracking"])
def receive_postback(request: Request, db: Session = Depends(get_db)):
    """
    S2S Postback from CPA networks.
    Accepts: click_id (or cid/clickid/aff_click_id), payout (or revenue/sum), status
    """
    p = dict(request.query_params)
    click_id_str = (
        p.get("click_id") or p.get("cid") or
        p.get("aff_click_id") or p.get("clickid")
    )
    payout = float(p.get("payout") or p.get("revenue") or p.get("sum") or 0)
    status_val = p.get("status", "confirmed")
    currency = p.get("currency", "USD")
    ip = _get_client_ip(request)

    # Audit log always
    pb = Postback(
        payout=payout,
        currency=currency,
        status=status_val,
        raw_params=p,
        ip=ip,
    )

    if click_id_str:
        try:
            click_uuid = uuid.UUID(click_id_str)
            pb.click_id = click_uuid
            click = db.query(Click).filter(Click.click_id == click_uuid).first()
            if click:
                pb.processed = True
                # Write conversion event
                event = Event(
                    link_id=click.link_id,
                    click_id=click_id_str,
                    event_name="conversion",
                    sub_id=click.sub_id,
                    creative_id=click.creative_id,
                    value=payout,
                    currency=currency,
                    country_code=click.country_code,
                    device_type=click.device_type,
                    ip=ip,
                    metadata_={"status": status_val, "source": "s2s_postback"},
                )
                db.add(event)
                # Update legacy stats: +1 conversion, +revenue
                stat = db.query(Stat).filter(
                    Stat.link_id == click.link_id,
                    Stat.sub_id == click.sub_id,
                    Stat.creative_id == click.creative_id,
                ).first()
                if stat:
                    stat.conversions += 1
                    stat.revenue = (stat.revenue or 0) + payout
                    db.add(stat)
        except ValueError:
            pass

    db.add(pb)
    db.commit()
    return Response(content="OK", media_type="text/plain")


# ─────────────────────────────────────────
# ANALYTICS
# ─────────────────────────────────────────
@app.get("/api/analytics/{campaign_id}/summary", response_model=StatsOut, tags=["Analytics"])
def analytics_summary(
    campaign_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.query(Stat).join(Stat.link).filter(
        Link.campaign_id == campaign_id
    ).all()
    total_clicks = sum(r.clicks for r in rows)
    total_conv = sum(r.conversions for r in rows)
    total_cost = float(sum(r.cost or 0 for r in rows))
    total_revenue = float(sum(r.revenue or 0 for r in rows))
    total_bounces = sum(r.bounces for r in rows)
    cr = (total_conv / total_clicks * 100) if total_clicks else 0
    roi = ((total_revenue - total_cost) / total_cost * 100) if total_cost else 0
    epc = (total_revenue / total_clicks) if total_clicks else 0
    return StatsOut(
        clicks=total_clicks, conversions=total_conv,
        bounces=total_bounces, cost=total_cost,
        revenue=total_revenue, roi=roi, cr=cr, epc=epc,
    )


@app.get("/api/analytics/{campaign_id}/by_source", tags=["Analytics"])
def analytics_by_source(
    campaign_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Top sub_ids by ROI for a campaign."""
    rows = db.query(Stat).join(Stat.link).filter(
        Link.campaign_id == campaign_id
    ).all()
    result = []
    for r in rows:
        cost = float(r.cost or 0)
        revenue = float(r.revenue or 0)
        roi = ((revenue - cost) / cost * 100) if cost else 0
        cr = (r.conversions / r.clicks * 100) if r.clicks else 0
        result.append({
            "sub_id": r.sub_id, "creative_id": r.creative_id,
            "clicks": r.clicks, "conversions": r.conversions,
            "cost": cost, "revenue": revenue, "roi": round(roi, 2), "cr": round(cr, 2),
        })
    return sorted(result, key=lambda x: x["roi"], reverse=True)


# ─────────────────────────────────────────
# AUTOPILOT RULES
# ─────────────────────────────────────────
@app.get("/api/campaigns/{campaign_id}/rules", response_model=List[RuleOut], tags=["AutoPilot"])
def list_rules(campaign_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Rule).filter(Rule.campaign_id == campaign_id).all()


@app.post("/api/campaigns/{campaign_id}/rules", response_model=RuleOut, status_code=201, tags=["AutoPilot"])
def create_rule(campaign_id: int, body: RuleCreate, current_user: User = Depends(require_role("admin", "manager")), db: Session = Depends(get_db)):
    rule = Rule(
        campaign_id=campaign_id,
        level=body.level,
        metric=body.metric,
        condition=body.condition,
        threshold=body.threshold,
        action=body.action,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@app.delete("/api/campaigns/{campaign_id}/rules/{rule_id}", tags=["AutoPilot"])
def delete_rule(campaign_id: int, rule_id: int, current_user: User = Depends(require_role("admin", "manager")), db: Session = Depends(get_db)):
    rule = db.query(Rule).filter(Rule.id == rule_id, Rule.campaign_id == campaign_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(rule)
    db.commit()
    return {"ok": True}


@app.get("/api/campaigns/{campaign_id}/rule_logs", tags=["AutoPilot"])
def get_rule_logs(campaign_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    logs = db.query(RuleLog).filter(RuleLog.campaign_id == campaign_id).order_by(RuleLog.triggered_at.desc()).limit(100).all()
    return [{"id": l.id, "rule_id": l.rule_id, "triggered_at": l.triggered_at,
             "metric_value": float(l.metric_value or 0), "action_taken": l.action_taken,
             "notification_sent": l.notification_sent} for l in logs]


# ─────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────
@app.get("/api/settings", tags=["Settings"])
def get_settings(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Setting).filter(Setting.user_id == current_user.id).all()
    return {r.key: r.value for r in rows}


@app.post("/api/settings", tags=["Settings"])
def upsert_settings(body: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    for key, value in body.items():
        setting = db.query(Setting).filter(
            Setting.user_id == current_user.id, Setting.key == key
        ).first()
        if setting:
            setting.value = str(value)
        else:
            setting = Setting(user_id=current_user.id, key=key, value=str(value))
            db.add(setting)
    db.commit()
    return {"ok": True}
