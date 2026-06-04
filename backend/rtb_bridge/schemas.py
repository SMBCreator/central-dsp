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
    btype: Optional[List[int]] = None
    battr: Optional[List[int]] = None
    pos: Optional[int] = None
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
    request: Optional[str] = None
    ver: Optional[str] = None
    battr: Optional[List[int]] = None


class Impression(BaseModel):
    id: str
    banner: Optional[Banner] = None
    video: Optional[Video] = None
    native: Optional[Native] = None
    displaymanager: Optional[str] = None
    displaymanagerver: Optional[str] = None
    instl: int = 0
    tagid: Optional[str] = None
    bidfloor: float = 0.0
    bidfloorcur: str = "USD"
    secure: Optional[int] = None
    exp: Optional[int] = None


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
    cat: Optional[List[str]] = None
    page: Optional[str] = None
    ref: Optional[str] = None
    publisher: Optional[Publisher] = None
    content: Optional[Content] = None
    keywords: Optional[str] = None


class App(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    bundle: Optional[str] = None
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
    country: Optional[str] = None
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
    devicetype: Optional[int] = None
    make: Optional[str] = None
    model: Optional[str] = None
    os: Optional[str] = None
    osv: Optional[str] = None
    browser: Optional[str] = None
    browserv: Optional[str] = None
    language: Optional[str] = None
    carrier: Optional[str] = None
    connectiontype: Optional[int] = None
    ifa: Optional[str] = None


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
    test: int = 0
    at: int = 2
    tmax: Optional[int] = None
    wseat: Optional[List[str]] = None
    bseat: Optional[List[str]] = None
    allimps: int = 0
    cur: Optional[List[str]] = None
    bcat: Optional[List[str]] = None
    badv: Optional[List[str]] = None
    bapp: Optional[List[str]] = None


# ──────────────────────────────────────────────────────────────
# OpenRTB 2.5 — Bid Response
# ──────────────────────────────────────────────────────────────

class Bid(BaseModel):
    id: str
    impid: str
    price: float
    nurl: Optional[str] = None
    lurl: Optional[str] = None
    adid: Optional[str] = None
    adomain: Optional[List[str]] = None
    bundle: Optional[str] = None
    iurl: Optional[str] = None
    cid: Optional[str] = None
    crid: Optional[str] = None
    cat: Optional[List[str]] = None
    attr: Optional[List[int]] = None
    api: Optional[int] = None
    adm: Optional[str] = None
    w: Optional[int] = None
    h: Optional[int] = None
    exp: Optional[int] = None


class SeatBid(BaseModel):
    bid: List[Bid] = []
    seat: Optional[str] = None
    group: int = 0


class BidResponse(BaseModel):
    id: str
    seatbid: List[SeatBid] = []
    bidid: Optional[str] = None
    cur: str = "USD"
    customdata: Optional[str] = None
    nbr: Optional[int] = None


# ──────────────────────────────────────────────────────────────
# Win / Loss Notice
# ──────────────────────────────────────────────────────────────

class WinNotice(BaseModel):
    auction_id: str
    imp_id: str
    campaign_id: int
    creative_id: int
    win_price: float
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
    loss_reason: Optional[int] = None


# ──────────────────────────────────────────────────────────────
# CentralDSP RTB API — формат кампании для биддера
# ──────────────────────────────────────────────────────────────

class CreativeOut(BaseModel):
    """
    Креатив в формате, который отдаётся Java-биддеру.
    Содержит все поля, необходимые AdMarkupService для генерации
    HTML/Native JSON/VAST разметки.
    """
    id: int
    format: str                             # banner | native | video
    width: Optional[int] = None
    height: Optional[int] = None

    # Banner
    image_url: Optional[str] = None
    click_url: Optional[str] = None

    # Native
    title: Optional[str] = None
    description: Optional[str] = None
    icon_url: Optional[str] = None
    sponsored_by: Optional[str] = None

    # Video
    vast_url: Optional[str] = None
    video_url: Optional[str] = None
    duration_sec: Optional[int] = None

    # Raw markup (overrides format-specific fields if set)
    adm: Optional[str] = None

    # Advertiser domain for adomain field in Bid
    adomain: Optional[str] = None

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
    hour_schedule: Dict[str, Any] = {}

    class Config:
        from_attributes = True


class RTBCampaignOut(BaseModel):
    """
    Полный формат кампании для Java-биддера.
    Java CampaignDTO должен иметь все эти поля — они используются
    BidEngineService и AdMarkupService.
    """
    id: int
    name: str
    status: str

    # Bid pricing — необходимы BidEngineService
    bid_price: Optional[float] = None       # базовая ставка CPM
    bid_floor: Optional[float] = None       # минимальная ставка
    bid_ceiling: Optional[float] = None     # максимальная ставка

    # Budget
    daily_budget: Optional[float] = None
    total_budget: Optional[float] = None
    budget_remaining: Optional[float] = None

    # Ad markup helpers — используются AdMarkupService
    click_url: Optional[str] = None         # fallback click URL для всех креативов
    tracking_pixel: Optional[str] = None    # 1x1 impression pixel

    # Frequency cap
    freq_cap: Optional[int] = None          # макс показов на пользователя

    # Targeting & creatives
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
