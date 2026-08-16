"""
OpenRTB 2.5 Pydantic schemas + CentralDSP RTB API schemas.
Ref: https://www.iab.com/wp-content/uploads/2016/03/OpenRTB-API-Specification-Version-2-5-FINAL.pdf
"""
from __future__ import annotations
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────
# OpenRTB 2.5 — Bid Request объекты
# ──────────────────────────────────────────────────────────────

class Format(BaseModel):
    w: Optional[int] = None
    h: Optional[int] = None


class Banner(BaseModel):
    w: Optional[int] = None
    h: Optional[int] = None
    format: Optional[List[Format]] = None
    btype: Optional[List[int]] = None   # запрещённые типы баннеров
    battr: Optional[List[int]] = None   # запрещённые атрибуты
    pos: Optional[int] = None           # позиция (0=unknown, 1=above fold...)
    mimes: Optional[List[str]] = None


class Video(BaseModel):
    mimes: List[str] = []
    minduration: Optional[int] = None
    maxduration: Optional[int] = None
    protocols: Optional[List[int]] = None
    w: Optional[int] = None
    h: Optional[int] = None
    linearity: Optional[int] = None
    skip: Optional[int] = None
    battr: Optional[List[int]] = None


class Native(BaseModel):
    request: Optional[str] = None       # JSON-строка нативного запроса
    ver: Optional[str] = None
    battr: Optional[List[int]] = None


class Impression(BaseModel):
    id: str
    banner: Optional[Banner] = None
    video: Optional[Video] = None
    native: Optional[Native] = None
    displaymanager: Optional[str] = None
    displaymanagerver: Optional[str] = None
    instl: int = 0                      # 1 = interstitial
    tagid: Optional[str] = None
    bidfloor: float = 0.0               # минимальная цена в CPM
    bidfloorcur: str = "USD"
    secure: Optional[int] = None        # 1 = https required
    exp: Optional[int] = None           # время жизни показа (сек)


class Publisher(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    domain: Optional[str] = None


class Content(BaseModel):
    id: Optional[str] = None
    title: Optional[str] = None
    language: Optional[str] = None


class Site(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    domain: Optional[str] = None
    cat: Optional[List[str]] = None     # IAB категории
    page: Optional[str] = None
    ref: Optional[str] = None
    publisher: Optional[Publisher] = None
    content: Optional[Content] = None
    keywords: Optional[str] = None


class App(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    bundle: Optional[str] = None        # com.example.app
    domain: Optional[str] = None
    storeurl: Optional[str] = None
    cat: Optional[List[str]] = None
    ver: Optional[str] = None
    publisher: Optional[Publisher] = None


class Geo(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None
    type: Optional[int] = None
    accuracy: Optional[int] = None
    country: Optional[str] = None       # ISO-3166-1 alpha-3
    region: Optional[str] = None
    city: Optional[str] = None
    zip: Optional[str] = None
    utcoffset: Optional[int] = None


class Device(BaseModel):
    ua: Optional[str] = None
    geo: Optional[Geo] = None
    dnt: Optional[int] = None
    lmt: Optional[int] = None
    ip: Optional[str] = None
    ipv6: Optional[str] = None
    devicetype: Optional[int] = None    # 1=mobile, 2=PC, 3=TV, 4=phone, 5=tablet
    make: Optional[str] = None
    model: Optional[str] = None
    os: Optional[str] = None
    osv: Optional[str] = None
    browser: Optional[str] = None
    browserv: Optional[str] = None
    language: Optional[str] = None
    carrier: Optional[str] = None
    connectiontype: Optional[int] = None  # 0=unknown, 1=ethernet, 2=wifi, 3=cellular
    ifa: Optional[str] = None           # IDFA/GAID


class User(BaseModel):
    id: Optional[str] = None
    buyeruid: Optional[str] = None
    yob: Optional[int] = None
    gender: Optional[str] = None
    keywords: Optional[str] = None
    customdata: Optional[str] = None


class BidRequest(BaseModel):
    id: str
    imp: List[Impression] = []
    site: Optional[Site] = None
    app: Optional[App] = None
    device: Optional[Device] = None
    user: Optional[User] = None
    test: int = 0                       # 1 = тестовый аукцион, не считается
    at: int = 2                         # тип аукциона: 1=first price, 2=second price
    tmax: Optional[int] = None          # timeout ms
    wseat: Optional[List[str]] = None   # whitelist seat
    bseat: Optional[List[str]] = None   # blacklist seat
    allimps: int = 0
    cur: Optional[List[str]] = None
    bcat: Optional[List[str]] = None    # запрещённые IAB категории
    badv: Optional[List[str]] = None    # запрещённые домены рекламодателей
    bapp: Optional[List[str]] = None    # запрещённые app bundle


# ──────────────────────────────────────────────────────────────
# OpenRTB 2.5 — Bid Response объекты
# ──────────────────────────────────────────────────────────────

class Bid(BaseModel):
    id: str
    impid: str                          # ID из BidRequest.imp[]
    price: float                        # CPM в USD
    nurl: Optional[str] = None          # Win Notice URL
    lurl: Optional[str] = None          # Loss Notice URL
    adid: Optional[str] = None
    adomain: Optional[List[str]] = None
    bundle: Optional[str] = None
    iurl: Optional[str] = None          # URL превью баннера
    cid: Optional[str] = None           # campaign ID
    crid: Optional[str] = None          # creative ID
    cat: Optional[List[str]] = None
    attr: Optional[List[int]] = None
    api: Optional[int] = None
    adm: Optional[str] = None           # рекламный материал (HTML/VAST/native JSON)
    w: Optional[int] = None
    h: Optional[int] = None
    exp: Optional[int] = None


class SeatBid(BaseModel):
    bid: List[Bid] = []
    seat: Optional[str] = None
    group: int = 0


class BidResponse(BaseModel):
    id: str                             # echo BidRequest.id
    seatbid: List[SeatBid] = []
    bidid: Optional[str] = None
    cur: str = "USD"
    customdata: Optional[str] = None
    nbr: Optional[int] = None           # No Bid Reason code


# ──────────────────────────────────────────────────────────────
# Win / Loss Notice
# ──────────────────────────────────────────────────────────────

class WinNotice(BaseModel):
    auction_id: str                     # BidRequest.id
    imp_id: str                         # Impression.id
    campaign_id: int
    creative_id: int
    win_price: float                    # реальная цена победы (CPM)
    clearing_price: Optional[float] = None
    site_domain: Optional[str] = None
    app_bundle: Optional[str] = None
    geo_country: Optional[str] = None
    device_type: Optional[str] = None


class LossNotice(BaseModel):
    auction_id: str
    imp_id: str
    campaign_id: int
    creative_id: Optional[int] = None
    loss_reason: Optional[int] = None   # OpenRTB loss reason codes
    # 0=error, 1=internal, 2=price, 3=blocked, 4=freq cap...


# ──────────────────────────────────────────────────────────────
# CentralDSP RTB API — Campaign format для биддера
# ──────────────────────────────────────────────────────────────

class CreativeOut(BaseModel):
    id: int
    format: str                         # banner|native|video
    width: Optional[int] = None
    height: Optional[int] = None
    image_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    click_url: Optional[str] = None
    adm: Optional[str] = None           # готовый HTML/VAST

    class Config:
        from_attributes = True


class TargetingOut(BaseModel):
    bid_floor: float = 0.0
    bid_ceiling: float = 10.0
    geo_countries: List[str] = []
    device_types: List[str] = []
    os_list: List[str] = []
    browser_list: List[str] = []
    site_whitelist: List[str] = []
    site_blacklist: List[str] = []
    hour_schedule: Dict[str, Any] = {}  # {"0": {"0": 1.0, "1": 0.5, ...}, ...}

    class Config:
        from_attributes = True


class RTBCampaignOut(BaseModel):
    """Формат кампании для RTB4FREE биддера."""
    id: int
    name: str
    status: str
    daily_budget: Optional[float] = None
    total_budget: Optional[float] = None
    budget_remaining: Optional[float] = None
    targeting: Optional[TargetingOut] = None
    creatives: List[CreativeOut] = []

    class Config:
        from_attributes = True


class BidderStatusOut(BaseModel):
    bidder_alive: bool
    endpoint: str
    qps: Optional[int] = None
    win_rate_pct: Optional[float] = None
    avg_latency_ms: Optional[int] = None
    auctions_today: Optional[int] = None
    wins_today: Optional[int] = None
    spend_today: Optional[float] = None


class RTBStatsOut(BaseModel):
    campaign_id: int
    auctions: int
    wins: int
    losses: int
    win_rate_pct: float
    avg_bid_price: float
    avg_win_price: float
    total_spend: float
    avg_cpm: float
