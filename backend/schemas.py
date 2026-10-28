"""Pydantic request/response schemas for the management API."""
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, field_validator

import rtb_compat
import url_macros
from targeting_contract import validate_targeting

# IAB Content Taxonomy 1.0: "IAB1" (tier-1) или "IAB1-1" (tier-2).
_IAB_CODE_RE = re.compile(r"^IAB\d{1,2}(-\d{1,2})?$")

# Пределы разметки креатива (audit R-05): что принято здесь — соберётся в adm
# обоими биддерами (rtb_compat.adm_incompat / adm.go admIncompat).
MAX_HTML_SNIPPET_LEN = 100_000
MAX_VAST_XML_LEN = 65_536


def validate_creative_url(value: str | None) -> str | None:
    """URL для разметки: абсолютный http(s) с хостом, <= 2048 символов, без
    пробелов, кавычек, угловых скобок (иначе XSS у паблишера или битый adm)."""
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    if not rtb_compat.safe_url(value):
        raise ValueError(
            "ожидается абсолютный http(s)-URL без пробелов, кавычек и угловых скобок "
            f"(до {rtb_compat.MAX_ADM_URL_LEN} символов)"
        )
    return value


def validate_vast_xml(value: str | None) -> str | None:
    """Пользовательский VAST: well-formed XML с корнем <VAST> и хотя бы одним
    <InLine>/<Wrapper> — иначе биддер не сможет вставить трекеры."""
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    if len(value) > MAX_VAST_XML_LEN:
        raise ValueError(f"vast_xml длиннее {MAX_VAST_XML_LEN} символов")
    try:
        rtb_compat.validate_vast(value)
    except ValueError as e:
        raise ValueError(f"vast_xml не разбирается как VAST: {e}") from e
    return value


def validate_creative_macros(value: str | None) -> str | None:
    """Неизвестный ${CDSP_…} (опечатка) — 422 с подсказкой. Макросы SSP и
    ${CLICK_URL…} не проверяются; для уже сохранённых креативов не запускается."""
    err = url_macros.validate_macros(value)
    if err:
        raise ValueError(err)
    return value


CreativeUrl = Annotated[str | None, AfterValidator(validate_creative_url)]
# Ссылка перехода и пиксели: дополнительно проверяем макросы каталога.
CreativeMacroUrl = Annotated[str | None, AfterValidator(validate_creative_url), AfterValidator(validate_creative_macros)]
VastXml = Annotated[str | None, AfterValidator(validate_vast_xml)]


# --- Auth -------------------------------------------------------------------
PASSWORD_MAX_BYTES = 72  # предел bcrypt; см. auth.PASSWORD_MAX_BYTES (audit S-04)


def validate_password_bytes(value: str) -> str:
    """max_length=128 считает символы; bcrypt — байты. 73+ байт (например,
    25 кириллических символов) раньше давали 500 из bcrypt — теперь 422."""
    if len(value.encode("utf-8")) > PASSWORD_MAX_BYTES:
        raise ValueError(f"пароль длиннее {PASSWORD_MAX_BYTES} байт в UTF-8")
    return value


PasswordStr = Annotated[str, Field(min_length=8, max_length=128), AfterValidator(validate_password_bytes)]


class RegisterIn(BaseModel):
    email: EmailStr
    password: PasswordStr
    name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class ProfilePatch(BaseModel):
    """H9: обновление имени в профиле."""

    name: str = Field(min_length=1, max_length=120)


class ChangePasswordIn(BaseModel):
    """H9: смена пароля пользователем."""

    current_password: str = Field(min_length=1, max_length=128)
    new_password: PasswordStr


# --- Advertisers ------------------------------------------------------------
def _emails_validator(v):
    if v is None:
        return None
    import notify_business
    return notify_business.normalise_emails(v)


class AdvertiserIn(BaseModel):
    # M-01: balance is NOT part of advertiser CRUD. Money enters only through
    # /billing/deposit and /billing/adjust (finance roles, ledger, idempotency).
    # extra="forbid" turns a client that still sends `balance` into a 422
    # instead of a silently ignored field.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    domain: str | None = None
    category: str | None = None
    currency: str = Field(default="RUB", pattern="^(USD|EUR|RUB|KZT|UZS)$")
    # C4b/N-03: адреса бизнес-уведомлений (пусто -> email владельца).
    notify_emails: list[str] = Field(default_factory=list, max_length=20)
    # ТЗ п.5: где планируется реклама. UI требует явный выбор; для старых
    # клиентов API по умолчанию 'ru' (поведение как до п.5).
    ad_market: Literal["ru", "foreign"] = "ru"

    @field_validator("notify_emails")
    @classmethod
    def _notify_emails(cls, v):
        return _emails_validator(v) or []


class AdvertiserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    domain: str | None = None
    category: str | None = None
    status: str | None = Field(default=None, pattern="^(active|paused|archived)$")
    # C4b/N-03: адреса бизнес-уведомлений (пусто -> email владельца).
    notify_emails: list[str] | None = Field(default=None, max_length=20)
    # ТЗ п.5: смена рынка — только superadmin, проверки в ad_market.change_ad_market
    ad_market: Literal["ru", "foreign"] | None = None

    @field_validator("notify_emails")
    @classmethod
    def _notify_emails(cls, v):
        return _emails_validator(v)


# --- Campaigns ---------------------------------------------------------------
class FreqCapWindow(BaseModel):
    impressions: int = Field(ge=1, le=1000)
    period_hours: int = Field(ge=1, le=720)


class CampaignIn(BaseModel):
    advertiser_id: str
    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(default="awareness", pattern="^(awareness|traffic|conversions)$")
    group_id: str | None = None
    daily_budget: float = Field(default=0, ge=0)
    total_budget: float = Field(default=0, ge=0)
    start_date: str | None = None
    end_date: str | None = None
    pacing: str = Field(default="even", pattern="^(even|asap)$")
    frequency_cap_impressions: int | None = Field(default=None, ge=1, le=100)
    frequency_cap_period_hours: int | None = Field(default=None, ge=1, le=168)
    frequency_caps: list[FreqCapWindow] | None = Field(default=None, max_length=3)
    # Тематика ККТУ (ОРД): код и подпись; привязывается к креативам при регистрации
    kktu_code: str | None = Field(default=None, max_length=20)
    kktu_label: str | None = Field(default=None, max_length=300)
    bid_shading: bool = False
    # Цепочка ОРД (ord_spec §7, B13d): кого рекламируем, рекламодатель, договоры.
    ord_mode: str | None = Field(default=None, pattern="^(self|own_client|partner_client)$")
    counterparty_id: str | None = None
    initial_contract_id: str | None = None
    executive_contract_id: str | None = None


class OrdChainPreview(BaseModel):
    """Проверка цепочки ОРД до создания кампании (мастер, шаг «Данные ОРД»)."""
    advertiser_id: str
    ord_mode: str | None = Field(default=None, pattern="^(self|own_client|partner_client)$")
    counterparty_id: str | None = None
    initial_contract_id: str | None = None
    executive_contract_id: str | None = None


class CampaignPatch(BaseModel):
    name: str | None = None
    objective: str | None = Field(default=None, pattern="^(awareness|traffic|conversions)$")
    status: str | None = Field(default=None, pattern="^(draft|active|paused|completed|archived)$")
    daily_budget: float | None = Field(default=None, ge=0)
    total_budget: float | None = Field(default=None, ge=0)
    start_date: str | None = None
    end_date: str | None = None
    pacing: str | None = Field(default=None, pattern="^(even|asap)$")
    frequency_cap_impressions: int | None = Field(default=None, ge=1, le=100)
    frequency_cap_period_hours: int | None = Field(default=None, ge=1, le=168)
    frequency_caps: list[FreqCapWindow] | None = Field(default=None, max_length=3)
    # Optimization: pixel-driven buying
    pixel_id: str | None = None
    goal_id: str | None = None
    target_cpa: float | None = Field(default=None, ge=0)
    target_cpc: float | None = Field(default=None, ge=0)
    target_cpm: float | None = Field(default=None, ge=0)
    target_cpv: float | None = Field(default=None, ge=0)
    auto_optimize: bool | None = None
    group_id: str | None = None
    kktu_code: str | None = Field(default=None, max_length=20)
    kktu_label: str | None = Field(default=None, max_length=300)
    bid_shading: bool | None = None
    # Clean-traffic (Часть B): закупать только домены с quality score >= порога
    clean_traffic_only: bool | None = None
    # Профиль скоринга качества площадок (Часть 3b)
    scoring_profile_id: str | None = None
    # Цепочка ОРД — до подтверждения (после → 409 ord_locked)
    ord_mode: str | None = Field(default=None, pattern="^(self|own_client|partner_client)$")
    counterparty_id: str | None = None
    initial_contract_id: str | None = None
    executive_contract_id: str | None = None
    # C4b/N-03: адреса бизнес-уведомлений кампании; notify_inherit=false — не
    # подниматься к рекламодателю/владельцу при пустом списке.
    notify_emails: list[str] | None = Field(default=None, max_length=20)
    notify_inherit: bool | None = None

    @field_validator("notify_emails")
    @classmethod
    def _notify_emails(cls, v):
        return _emails_validator(v)


# --- Line items ---------------------------------------------------------------
class LineItemIn(BaseModel):
    campaign_id: str
    name: str = Field(min_length=1, max_length=200)
    bid_type: str = Field(default="cpm", pattern="^(cpm|cpc)$")
    bid_price: float = Field(ge=0.01, le=1000)
    max_bid: float | None = Field(default=None, ge=0.01, le=1000)
    daily_budget: float = Field(default=0, ge=0)
    targeting: dict = Field(default_factory=dict)

    @field_validator("targeting")
    @classmethod
    def _targeting_contract(cls, v: dict) -> dict:
        return validate_targeting(v)


class LineItemPatch(BaseModel):
    name: str | None = None
    status: str | None = Field(default=None, pattern="^(active|paused|archived)$")
    bid_type: str | None = Field(default=None, pattern="^(cpm|cpc)$")
    bid_price: float | None = Field(default=None, ge=0.01, le=1000)
    max_bid: float | None = Field(default=None, ge=0.01, le=1000)
    daily_budget: float | None = Field(default=None, ge=0)
    targeting: dict | None = None

    @field_validator("targeting")
    @classmethod
    def _targeting_contract(cls, v: dict | None) -> dict | None:
        return None if v is None else validate_targeting(v)


class LineItemCreativesIn(BaseModel):
    """G9: список креативов, привязанных к line item. Пустой список = все креативы кампании."""

    creative_ids: list[str] = Field(default_factory=list, max_length=500)


# --- Creatives -----------------------------------------------------------------
class CreativeIn(BaseModel):
    campaign_id: str
    line_item_id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(default="banner", pattern="^(banner|native|video|push)$")
    # banner: static|gif|html5 ; video: instream|outstream ; push/native: null
    subtype: str | None = Field(default=None, pattern="^(static|gif|html5|instream|outstream)$")
    width: int | None = Field(default=None, ge=1, le=4096)
    height: int | None = Field(default=None, ge=1, le=4096)
    asset_url: CreativeUrl = None
    click_url: CreativeMacroUrl = None
    html_snippet: str | None = Field(default=None, max_length=MAX_HTML_SNIPPET_LEN)
    native_title: str | None = Field(default=None, max_length=90)
    native_description: str | None = Field(default=None, max_length=200)
    native_cta: str | None = Field(default=None, max_length=30)
    native_icon_url: CreativeUrl = None
    video_url: CreativeUrl = None
    video_duration: int | None = Field(default=None, ge=1, le=600)
    vast_xml: VastXml = None
    crop: dict | None = None
    # Сторонние трекеры (показ и клик) — пиксели аудита
    imp_tracker_url: CreativeMacroUrl = None
    click_tracker_url: CreativeMacroUrl = None
    # Информация о рекламодателе (контрагент из ОРД): ОГРН, наименование, адрес
    counterparty_id: str | None = None
    # Договор ОРД для получения erid (миграция 036). Пусто — подбирается по контрагенту.
    contract_id: str | None = None
    # ККТУ-классификатор объявления (маркировка), см. миграцию 024
    kktu_code: str | None = Field(default=None, max_length=20)
    kktu_label: str | None = Field(default=None, max_length=300)
    # Brand-safety сигналы для SSP (миграция 035): bid.cat / фильтр bcat;
    # bid.attr / фильтр imp.*.battr. Коды IAB 1.0 вида "IAB1" / "IAB1-1", attrs 1..17.
    iab_categories: list[str] = Field(default_factory=list, max_length=10)
    attrs: list[int] = Field(default_factory=list, max_length=17)

    @field_validator("iab_categories")
    @classmethod
    def _iab_codes(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for code in v:
            code = code.strip().upper()
            if not _IAB_CODE_RE.match(code):
                raise ValueError(f"Некорректный IAB-код: {code!r} (ожидается IAB1 или IAB1-1)")
            if code not in out:
                out.append(code)
        return out

    @field_validator("attrs")
    @classmethod
    def _attr_codes(cls, v: list[int]) -> list[int]:
        bad = [a for a in v if not 1 <= a <= 17]
        if bad:
            raise ValueError(f"OpenRTB creative attribute вне диапазона 1..17: {bad}")
        return sorted(set(v))


# Пакетное создание баннеров (POST /creatives/batch): общие поля задаются один
# раз, по файлу — название, своя ссылка перехода, файл и размер.
CREATIVE_BATCH_MAX = 30


class CreativeBatchCommon(BaseModel):
    subtype: str | None = Field(default=None, pattern="^(static|gif|html5)$")
    click_url: CreativeMacroUrl = None
    imp_tracker_url: CreativeMacroUrl = None
    click_tracker_url: CreativeMacroUrl = None
    counterparty_id: str | None = None
    contract_id: str | None = None
    kktu_code: str | None = Field(default=None, max_length=20)
    kktu_label: str | None = Field(default=None, max_length=300)
    iab_categories: list[str] = Field(default_factory=list, max_length=10)
    attrs: list[int] = Field(default_factory=list, max_length=17)

    _iab_codes = field_validator("iab_categories")(CreativeIn._iab_codes.__func__)
    _attr_codes = field_validator("attrs")(CreativeIn._attr_codes.__func__)


class CreativeBatchItem(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    click_url: str | None = None
    # Свои пиксели элемента. Проверяются при сборке CreativeIn, чтобы неверный URL
    # давал ошибку только этого элемента, а не 422 на весь пакет. Пустая строка
    # или не задано — берётся значение из common.
    imp_tracker_url: str | None = Field(default=None, max_length=2048)
    click_tracker_url: str | None = Field(default=None, max_length=2048)
    asset_url: str = Field(min_length=1, max_length=2048)
    width: int = Field(ge=1, le=4096)
    height: int = Field(ge=1, le=4096)


class CreativeBatchIn(BaseModel):
    campaign_id: str
    line_item_id: str | None = None
    common: CreativeBatchCommon = Field(default_factory=CreativeBatchCommon)
    items: list[CreativeBatchItem] = Field(min_length=1, max_length=CREATIVE_BATCH_MAX)

    @field_validator("items")
    @classmethod
    def _unique_client_ids(cls, v: list[CreativeBatchItem]) -> list[CreativeBatchItem]:
        ids = [i.client_id for i in v]
        if len(ids) != len(set(ids)):
            raise ValueError("client_id элементов пакета должны быть уникальны")
        return v


class CreativePatch(BaseModel):
    name: str | None = None
    counterparty_id: str | None = None
    contract_id: str | None = None
    kktu_code: str | None = Field(default=None, max_length=20)
    kktu_label: str | None = Field(default=None, max_length=300)
    iab_categories: list[str] | None = Field(default=None, max_length=10)
    attrs: list[int] | None = Field(default=None, max_length=17)

    @field_validator("iab_categories")
    @classmethod
    def _iab_codes(cls, v):
        return None if v is None else CreativeIn._iab_codes(v)

    @field_validator("attrs")
    @classmethod
    def _attr_codes(cls, v):
        return None if v is None else CreativeIn._attr_codes(v)
    imp_tracker_url: CreativeMacroUrl = None
    click_tracker_url: CreativeMacroUrl = None
    # Владелец может только отправить на модерацию или архивировать.
    # approved/rejected ставит исключительно /moderation (админ) — иначе
    # обход модерации и показ непроверенного креатива.
    status: str | None = Field(default=None, pattern="^(pending|archived)$")
    asset_url: CreativeUrl = None
    click_url: CreativeMacroUrl = None
    html_snippet: str | None = Field(default=None, max_length=MAX_HTML_SNIPPET_LEN)
    native_title: str | None = Field(default=None, max_length=90)
    native_description: str | None = Field(default=None, max_length=200)
    native_cta: str | None = Field(default=None, max_length=30)
    native_icon_url: CreativeUrl = None
    video_url: CreativeUrl = None
    video_duration: int | None = Field(default=None, ge=1, le=600)
    vast_xml: VastXml = None


# --- Supply partners --------------------------------------------------------------
class PartnerIdentityIn(BaseModel):
    """ID sync партнёра (план predeploy A.5). Глубокая проверка — id_sync.validate_identity_config."""
    sync_enabled: bool = False
    sync_in_param: str = Field(default="ssp_uid", max_length=32)
    sync_callback_url_template: str | None = Field(default=None, max_length=1024)
    sync_redirect_hosts: list[str] = Field(default_factory=list, max_length=16)
    id_source: str = Field(default="user_id", max_length=80)
    ttl_days: int = Field(default=90, ge=1, le=395)
    requires_consent: bool = True


class SupplyPartnerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    auction_type: int = Field(default=2, ge=1, le=2)
    bid_floor: float = Field(default=0, ge=0)
    qps_limit: int = Field(default=100, ge=1, le=100000)
    allowed_ad_types: list[str] = Field(default_factory=lambda: ["banner", "native", "video", "push"])
    notes: str | None = None
    # Онбординг-анкета SSP (миграция 030)
    protocol_version: str = Field(default="2.5", pattern="^(2\\.5|2\\.6)$")
    currency: str = Field(default="RUB", pattern="^(RUB|USD|EUR)$")
    tmax_ms: int = Field(default=200, ge=50, le=1000)
    ip_whitelist: list[str] = Field(default_factory=list, max_length=64)
    seat_id: str | None = Field(default=None, max_length=100)
    contact_email: str | None = Field(default=None, max_length=200)
    # R-07/R-06: событие расхода и основание freq cap — независимые решения
    # per SSP (см. миграции 046/043). None -> DEFAULT БД ('burl' / false).
    billing_event: str | None = Field(default=None, pattern="^(win|burl)$")
    freq_cap_on_win: bool | None = None
    identity: PartnerIdentityIn | None = None


class SupplyPartnerPatch(BaseModel):
    name: str | None = None
    status: str | None = Field(default=None, pattern="^(draft|testing|active|paused|archived)$")
    auction_type: int | None = Field(default=None, ge=1, le=2)
    bid_floor: float | None = Field(default=None, ge=0)
    qps_limit: int | None = Field(default=None, ge=1, le=100000)
    allowed_ad_types: list[str] | None = None
    notes: str | None = None
    protocol_version: str | None = Field(default=None, pattern="^(2\\.5|2\\.6)$")
    currency: str | None = Field(default=None, pattern="^(RUB|USD|EUR)$")
    tmax_ms: int | None = Field(default=None, ge=50, le=1000)
    ip_whitelist: list[str] | None = Field(default=None, max_length=64)
    seat_id: str | None = Field(default=None, max_length=100)
    contact_email: str | None = Field(default=None, max_length=200)
    billing_event: str | None = Field(default=None, pattern="^(win|burl)$")
    freq_cap_on_win: bool | None = None
    identity: PartnerIdentityIn | None = None


# --- Rules ---------------------------------------------------------------------
class RuleIn(BaseModel):
    campaign_id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    metric: str = Field(pattern="^(ctr|cpm|cpc|cpa|cvr|spend|impressions|clicks|conversions|win_rate|budget_spent_pct)$")
    operator: str = Field(pattern="^(gt|lt|gte|lte)$")
    threshold: float
    action: str = Field(pattern="^(pause_campaign|pause_line_item|decrease_bid|increase_bid|decrease_daily_budget|increase_daily_budget|blacklist_domains|notify)$")
    action_value: float | None = None
    window_hours: int = Field(default=24, ge=1, le=168)


class RulePatch(BaseModel):
    name: str | None = None
    status: str | None = Field(default=None, pattern="^(active|paused|archived)$")
    metric: str | None = Field(default=None, pattern="^(ctr|cpm|cpc|cpa|cvr|spend|impressions|clicks|conversions|win_rate|budget_spent_pct)$")
    operator: str | None = Field(default=None, pattern="^(gt|lt|gte|lte)$")
    threshold: float | None = None
    action: str | None = Field(default=None, pattern="^(pause_campaign|pause_line_item|decrease_bid|increase_bid|decrease_daily_budget|increase_daily_budget|blacklist_domains|notify)$")
    action_value: float | None = None
    window_hours: int | None = Field(default=None, ge=1, le=168)


# --- Scoring profiles -------------------------------------------------------
class ScoringProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    window_days: int = Field(default=7, ge=1, le=90)
    min_impressions: int = Field(default=200, ge=0)
    min_clicks: int = Field(default=0, ge=0)
    min_ctr: float | None = Field(default=None, ge=0, le=100)
    min_viewability: float | None = Field(default=None, ge=0, le=100)
    max_bounce_rate: float | None = Field(default=None, ge=0, le=100)
    min_avg_depth: float | None = Field(default=None, ge=0)
    min_time_on_site: int | None = Field(default=None, ge=0)
    max_cpa: float | None = Field(default=None, ge=0)
    goal_id: str | None = None
    pixel_id: str | None = None
    block_threshold: float = Field(default=0.5, ge=0, le=1)
    enforce: bool = False


class ScoringProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    window_days: int | None = Field(default=None, ge=1, le=90)
    min_impressions: int | None = Field(default=None, ge=0)
    min_clicks: int | None = Field(default=None, ge=0)
    min_ctr: float | None = Field(default=None, ge=0, le=100)
    min_viewability: float | None = Field(default=None, ge=0, le=100)
    max_bounce_rate: float | None = Field(default=None, ge=0, le=100)
    min_avg_depth: float | None = Field(default=None, ge=0)
    min_time_on_site: int | None = Field(default=None, ge=0)
    max_cpa: float | None = Field(default=None, ge=0)
    goal_id: str | None = None
    pixel_id: str | None = None
    block_threshold: float | None = Field(default=None, ge=0, le=1)
    enforce: bool | None = None
