export interface User {
  id: string
  email: string
  name: string
  role: string
}

export interface Advertiser {
  id: string
  display_id: number
  name: string
  domain: string | null
  category: string | null
  status: string
  balance: number
  currency: string
  created_at: string
}

export interface Campaign {
  id: string
  display_id: number
  advertiser_id: string
  name: string
  objective: string
  status: string
  daily_budget: number
  total_budget: number
  spent_today: number
  spent_total: number
  start_date: string | null
  end_date: string | null
  pacing: string
  frequency_cap_impressions: number | null
  frequency_cap_period_hours: number | null
  created_at: string
}

export interface LineItem {
  id: string
  display_id: number
  campaign_id: string
  name: string
  status: string
  bid_type: string
  bid_price: number
  max_bid: number | null
  daily_budget: number
  spent_today: number
  targeting: Targeting
  created_at: string
}

export interface Targeting {
  geo_countries?: string[]
  geo_regions?: string[]
  geo_cities?: string[]
  device_types?: string[]
  os?: string[]
  browsers?: string[]
  languages?: string[]
  connection_types?: string[]
  iab_categories?: string[]
  iab_block?: string[]
  domain_allowlist?: string[]
  domain_blocklist?: string[]
  age_min?: number
  age_max?: number
  genders?: string[]
  hours?: number[]
  days?: number[]
  ssp_allowlist?: string[]
  audience_include?: string[]
  audience_exclude?: string[]
  ad_types?: string[]
  /* Inventory environment & supply quality */
  traffic_types?: string[] // "site" | "app"
  carriers?: string[]
  os_version_min?: string
  app_bundles_allow?: string[]
  app_bundles_block?: string[]
  publisher_allow?: string[]
  publisher_block?: string[]
  keywords_allow?: string[]
  keywords_block?: string[]
  ip_ranges?: string[]
  /* Slot-level */
  banner_positions?: number[] // 1 = above fold, 3 = below fold
  interstitial_only?: boolean
  exclude_interstitial?: boolean
  rewarded_only?: boolean
  secure_only?: boolean
  video_min_duration?: number
  video_skippable_only?: boolean
  /* PMP */
  deal_ids?: string[]
  /* Regulatory & privacy */
  block_coppa?: boolean
  block_gdpr?: boolean
  skip_dnt?: boolean
}

export const IAB_CATEGORIES: Record<string, string> = {
  "IAB1": "Искусство и развлечения",
  "IAB2": "Автомобили",
  "IAB3": "Бизнес",
  "IAB4": "Карьера",
  "IAB5": "Образование",
  "IAB6": "Семья и родители",
  "IAB7": "Здоровье и фитнес",
  "IAB8": "Еда и напитки",
  "IAB9": "Хобби и интересы",
  "IAB10": "Дом и сад",
  "IAB11": "Право и политика",
  "IAB12": "Новости",
  "IAB13": "Личные финансы",
  "IAB14": "Общество",
  "IAB15": "Наука",
  "IAB16": "Домашние животные",
  "IAB17": "Спорт",
  "IAB18": "Стиль и мода",
  "IAB19": "Технологии",
  "IAB20": "Путешествия",
  "IAB21": "Недвижимость",
  "IAB22": "Шопинг",
  "IAB23": "Религия",
  "IAB24": "Без категории",
  "IAB25": "Небезопасный контент",
  "IAB26": "Нелегальный контент",
}

export interface Creative {
  id: string
  display_id: number
  campaign_id: string
  line_item_id: string | null
  name: string
  type: "banner" | "native" | "video" | "push"
  status: string
  width: number | null
  height: number | null
  asset_url: string | null
  click_url: string | null
  html_snippet: string | null
  native_title: string | null
  native_description: string | null
  native_cta: string | null
  native_icon_url: string | null
  video_url: string | null
  video_duration: number | null
  vast_xml: string | null
  counterparty_id: string | null
  kktu_code: string | null
  kktu_label: string | null
  erid: string | null
  moderation_comment: string | null
  created_at: string
}

export interface SspCheckItem {
  key: string
  label: string
  ok: boolean
  detail: string
}

export interface SspCheckResult {
  ran_at: string
  passed: boolean
  checks: SspCheckItem[]
}

export interface SupplyPartner {
  id: string
  display_id: number
  name: string
  endpoint_key: string
  status: string
  protocol: string
  auction_type: number
  bid_floor: number
  qps_limit: number
  allowed_ad_types: string[]
  notes: string | null
  created_at: string
  // Онбординг SSP (миграция 030)
  protocol_version?: string
  currency?: string
  tmax_ms?: number
  ip_whitelist?: string[]
  seat_id?: string | null
  contact_email?: string | null
  has_auth_token?: boolean
  auth_token_hint?: string | null
  last_check?: SspCheckResult | null
}

export interface Rule {
  id: string
  campaign_id: string | null
  name: string
  status: string
  metric: string
  operator: string
  threshold: number
  action: string
  action_value: number | null
  window_hours: number
  last_triggered_at: string | null
  created_at: string
}

export interface StatsSummary {
  bids: number
  wins: number
  impressions: number
  clicks: number
  spend: number
  win_rate: number
  ctr: number
  ecpm: number
}

export interface TimeseriesPoint {
  ts: string
  bids: number
  wins: number
  impressions: number
  clicks: number
  spend: number
}

export interface BreakdownRow {
  id: string
  name: string
  status: string
  bids: number
  wins: number
  impressions: number
  clicks: number
  viewable_imps?: number
  spend: number
  win_rate: number
  ctr: number
  ecpm: number
  viewability?: number
}

// H7: единый формат денег по ТЗ — «12 500,50 ₽», разряды пробелами
export const fmtMoney = (v: number, currency = "RUB") =>
  new Intl.NumberFormat("ru-RU", { style: "currency", currency, maximumFractionDigits: 2 }).format(v)

export const fmtNum = (v: number) => new Intl.NumberFormat("ru-RU").format(v)

// Backend already returns percentages (e.g. win_rate 58.33 means 58.33%)
export const fmtPct = (v: number) => `${v.toFixed(2)}%`

export const STATUS_LABELS: Record<string, string> = {
  active: "Активна",
  paused: "Пауза",
  draft: "Черновик",
  archived: "Архив",
  pending: "На модерации",
  approved: "Одобрен",
  rejected: "Отклонён",
  completed: "Завершена",
  testing: "Тестирование",
}

export const AD_TYPE_LABELS: Record<string, string> = {
  banner: "Баннер",
  native: "Натив",
  video: "Видео",
  push: "Push",
}


export interface Counterparty {
  id: string
  name: string
  inn: string | null
  ogrn: string | null
  legal_form: string | null
  address: string | null
  role: string | null
  created_at: string
}

// --- Скоринг качества площадок (Часть 3b) ---
export interface ScoringProfile {
  id: string | null
  name: string
  description: string | null
  window_days: number
  min_impressions: number
  min_clicks: number
  min_ctr: number | null
  min_viewability: number | null
  max_bounce_rate: number | null
  min_avg_depth: number | null
  min_time_on_site: number | null
  max_cpa: number | null
  goal_id: string | null
  pixel_id: string | null
  block_threshold: number
  enforce: boolean
  created_at?: string
  updated_at?: string
}

export interface ScoringRow {
  domain?: string
  id?: string
  name?: string
  impressions: number
  clicks: number
  viewable_imps: number
  spend: number
  conversions?: number
  ctr: number
  viewability: number
  avg_depth: number | null
  avg_time: number | null
  bounce_rate: number | null
  cpa: number | null
  score: number | null
  reasons: string[]
  flagged: boolean
  blocked?: boolean
  block_source?: "auto" | "manual" | null
}

export type ScoringSortKey =
  | "score" | "impressions" | "clicks" | "ctr" | "viewability" | "cpa"
  | "bounce_rate" | "avg_depth" | "avg_time" | "spend" | "domain" | "name"

export interface ScoringReport {
  profile: ScoringProfile
  dimension: "domain" | "ssp"
  rows: ScoringRow[]
  total: number
  total_flagged: number
  total_blocked: number
  page: number
  page_size: number
  sort: ScoringSortKey
  order: "asc" | "desc"
  window_days: number
  generated_at: string
}
