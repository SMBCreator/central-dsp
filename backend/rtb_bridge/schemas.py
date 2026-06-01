"""OpenRTB 2.5 Pydantic schemas + CentralDSP internal bridge schemas.

OpenRTB 2.5 spec: https://www.iab.com/guidelines/openrtb/
We implement the subset required for banner/native/video DSP bidding.
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


# =============================================================================
# OpenRTB 2.5 — BID REQUEST (SSP → DSP)
# =============================================================================

class Banner(BaseModel):
    """OpenRTB 2.5 §3.2.6"""
    w: Optional[int] = None
    h: Optional[int] = None
    wmin: Optional[int] = None
    hmin: Optional[int] = None
    wmax: Optional[int] = None
    hmax: Optional[int] = None
    format: Optional[List[Dict[str, int]]] = None  # [{w,h},...]
    btype: Optional[List[int]] = None   # blocked banner types
    battr: Optional[List[int]] = None   # blocked creative attributes
    pos: Optional[int] = None           # ad position
    mimes: Optional[List[str]] = None
    api: Optional[List[int]] = None


class Video(BaseModel):
    """OpenRTB 2.5 §3.2.7"""
    mimes: List[str] = []
    minduration: Optional[int] = None
    maxduration: Optional[int] = None
    protocols: Optional[List[int]] = None
    w: Optional[int] = None
    h: Optional[int] = None
    linearity: Optional[int] = None
    skip: Optional[int] = None
    battr: Optional[List[int]] = None
    api: Optional[List[int]] = None


class Native(BaseModel):
    """OpenRTB 2.5 §3.2.9"""
    request: Optional[str] = None   # JSON-encoded native request
    ver: Optional[str] = None
    api: Optional[List[int]] = None
    battr: Optional[List[int]] = None


class Imp(BaseModel):
    """OpenRTB 2.5 §3.2.4 — Impression object"""
    id: str
    banner: Optional[Banner] = None
    video: Optional[Video] = None
    native: Optional[Native] = None
    displaymanager: Optional[str] = None
    displaymanagerver: Optional[str] = None
    instl: int = 0                  # 1 = interstitial
    tagid: Optional[str] = None
    bidfloor: float = 0.0
    bidfloorcur: str = "USD"
    secure: Optional[int] = None    # 1 = HTTPS required
    iframebuster: Optional[List[str]] = None
    pmp: Optional[Dict[str, Any]] = None
    ext: Optional[Dict[str, Any]] = None


class Publisher(BaseModel):
    """OpenRTB 2.5 §3.2.15"""
    id: Optional[str] = None
    name: Optional[str] = None
    domain: Optional[str] = None
    cat: Optional[List[str]] = None


class Site(BaseModel):
    """OpenRTB 2.5 §3.2.13 — Web inventory"""
    id: Optional[str] = None
    name: Optional[str] = None
    domain: Optional[str] = None
    cat: Optional[List[str]] = None
    sectioncat: Optional[List[str]] = None
    pagecat: Optional[List[str]] = None
    page: Optional[str] = None
    ref: Optional[str] = None
    search: Optional[str] = None
    mobile: Optional[int] = None
    privacypolicy: Optional[int] = None
    publisher: Optional[Publisher] = None
    keywords: Optional[str] = None
    ext: Optional[Dict[str, Any]] = None


class App(BaseModel):
    """OpenRTB 2.5 §3.2.14 — Mobile app inventory"""
    id: Optional[str] = None
    name: Optional[str] = None
    bundle: Optional[str] = None    # com.example.app
    domain: Optional[str] = None
    storeurl: Optional[str] = None
    cat: Optional[List[str]] = None
    ver: Optional[str] = None
    privacypolicy: Optional[int] = None
    paid: Optional[int] = None
    publisher: Optional[Publisher] = None
    keywords: Optional[str] = None
    ext: Optional[Dict[str, Any]] = None


class Geo(BaseModel):
    """OpenRTB 2.5 §3.2.19"""
    lat: Optional[float] = None
    lon: Optional[float] = None
    type: Optional[int] = None
    accuracy: Optional[int] = None
    country: Optional[str] = None   # ISO 3166-1 alpha-3, e.g. "RUS"
    region: Optional[str] = None
    metro: Optional[str] = None
    city: Optional[str] = None
    zip: Optional[str] = None
    utcoffset: Optional[int] = None
    ext: Optional[Dict[str, Any]] = None


class Device(BaseModel):
    """OpenRTB 2.5 §3.2.18"""
    ua: Optional[str] = None
    geo: Optional[Geo] = None
    dnt: Optional[int] = None
    lmt: Optional[int] = None
    ip: Optional[str] = None
    ipv6: Optional[str] = None
    devicetype: Optional[int] = None  # 1=mobile,2=pc,3=tv,4=phone,5=tablet,6=connected,7=stb
    make: Optional[str] = None
    model: Optional[str] = None
    os: Optional[str] = None        # "ios","android","windows","macos"
    osv: Optional[str] = None
    hwv: Optional[str] = None
    w: Optional[int] = None
    h: Optional[int] = None
    ppi: Optional[int] = None
    pxratio: Optional[float] = None
    js: Optional[int] = None
    language: Optional[str] = None
    carrier: Optional[str] = None
    connectiontype: Optional[int] = None
    ifa: Optional[str] = None       # advertising ID (IDFA/GAID)
    ext: Optional[Dict[str, Any]] = None


class User(BaseModel):
    """OpenRTB 2.5 §3.2.20"""
    id: Optional[str] = None
    buyeruid: Optional[str] = None
    yob: Optional[int] = None
    gender: Optional[str] = None
    keywords: Optional[str] = None
    customdata: Optional[str] = None
    geo: Optional[Geo] = None
    data: Optional[List[Dict[str, Any]]] = None
    ext: Optional[Dict[str, Any]] = None


class BidRequest(BaseModel):
    """OpenRTB 2.5 §3.2.1 — Top-level Bid Request"""
    id: str
    imp: List[Imp] = []
    site: Optional[Site] = None
    app: Optional[App] = None
    device: Optional[Device] = None
    user: Optional[User] = None
    test: int = 0               # 1 = test mode, no billing
    at: int = 2                 # auction type: 1=first price, 2=second price
    tmax: Optional[int] = None  # max latency ms
    wseat: Optional[List[str]] = None
    allimps: int = 0
    cur: Optional[List[str]] = None
    bcat: Optional[List[str]] = None    # blocked IAB categories
    badv: Optional[List[str]] = None    # blocked advertiser domains
    regs: Optional[Dict[str, Any]] = None
    ext: Optional[Dict[str, Any]] = None


# =============================================================================
# OpenRTB 2.5 — BID RESPONSE (DSP → SSP)
# =============================================================================

class Bid(BaseModel):
    """OpenRTB 2.5 §4.2.3 — Bid object"""
    id: str
    impid: str
    price: float                    # CPM bid price in USD
    adid: Optional[str] = None
    nurl: Optional[str] = None      # win notice URL (macro: ${AUCTION_PRICE})
    burl: Optional[str] = None      # billing notice URL
    lurl: Optional[str] = None      # loss notice URL
    adm: Optional[str] = None       # ad markup (HTML/VAST/native JSON)
    adomain: List[str] = []
    bundle: Optional[str] = None
    iurl: Optional[str] = None      # image URL for audit
    cid: Optional[str] = None       # campaign ID
    crid: Optional[str] = None      # creative ID
    cat: Optional[List[str]] = None
    attr: Optional[List[int]] = None
    w: Optional[int] = None
    h: Optional[int] = None
    api: Optional[int] = None
    ext: Optional[Dict[str, Any]] = None


class SeatBid(BaseModel):
    """OpenRTB 2.5 §4.2.2"""
    bid: List[Bid]
    seat: Optional[str] = None
    group: int = 0
    ext: Optional[Dict[str, Any]] = None


class BidResponse(BaseModel):
    """OpenRTB 2.5 §4.2.1 — Top-level Bid Response"""
    id: str                         # mirrors BidRequest.id
    seatbid: List[SeatBid] = []
    bidid: Optional[str] = None
    cur: str = "USD"
    customdata: Optional[str] = None
    nbr: Optional[int] = None       # no-bid reason code
    ext: Optional[Dict[str, Any]] = None


# =============================================================================
# CentralDSP internal bridge schemas
# =============================================================================

class CentralCreative(BaseModel):
    """Creative format served to internal bidder"""
    id: str
    campaign_id: str
    format: str                        # banner | native | video | push
    width: Optional[int] = None
    height: Optional[int] = None
    image_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    click_url: str
    adm: Optional[str] = None
    price_floor: float = 0.01
    max_bid: float = 5.00


class CentralTargeting(BaseModel):
    """Targeting rules for internal bidder"""
    geo_countries: List[str] = []
    device_types: List[str] = []      # mobile | desktop | tablet | ctv
    os_list: List[str] = []
    browser_list: List[str] = []
    site_whitelist: List[str] = []
    site_blacklist: List[str] = []
    hour_schedule: Dict[str, List[int]] = {}  # {"1": [9,10,...18]}
    bid_floor: float = 0.01
    bid_ceiling: float = 5.00


class CentralCampaign(BaseModel):
    """Campaign format served to internal bidder"""
    id: str
    name: str
    status: str
    daily_budget: Optional[float] = None
    total_budget: Optional[float] = None
    targeting: CentralTargeting
    creatives: List[CentralCreative]
    win_notice_url: str
    loss_notice_url: str


class CentralCampaignFeed(BaseModel):
    """Response to bidder campaign polling"""
    campaigns: List[CentralCampaign]
    generated_at: datetime
    version: str = "1.0"


class WinNotice(BaseModel):
    bid_request_id: str
    imp_id: str
    campaign_id: str
    creative_id: str
    bid_price: float
    win_price: float
    clearing_price: float
    ssp_name: Optional[str] = None
    site_domain: Optional[str] = None
    app_bundle: Optional[str] = None
    geo_country: Optional[str] = None
    device_type: Optional[str] = None


class LossNotice(BaseModel):
    bid_request_id: str
    imp_id: str
    campaign_id: str
    creative_id: str
    bid_price: float
    loss_reason: Optional[int] = None   # OpenRTB NBR loss reason code
    ssp_name: Optional[str] = None
    site_domain: Optional[str] = None
    geo_country: Optional[str] = None
    device_type: Optional[str] = None


class RTBStats(BaseModel):
    auctions_total: int = 0
    auctions_won: int = 0
    win_rate: float = 0.0
    impressions: int = 0
    avg_win_price: float = 0.0
    avg_bid_price: float = 0.0
    spend_today: float = 0.0
    cpm: float = 0.0
