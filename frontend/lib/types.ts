export interface User {
  id: string
  email: string
  name: string
  role: string
}

export interface Advertiser {
  id: string
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
  device_types?: string[]
  ad_types?: string[]
  domains_allow?: string[]
  domains_block?: string[]
  hours?: number[]
}

export interface Creative {
  id: string
  campaign_id: string
  line_item_id: string | null
  name: string
  type: "banner" | "native" | "video"
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
  created_at: string
}

export interface SupplyPartner {
  id: string
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
  spend: number
  win_rate: number
  ctr: number
  ecpm: number
}

export const fmtMoney = (v: number, currency = "USD") =>
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
}

export const AD_TYPE_LABELS: Record<string, string> = {
  banner: "Баннер",
  native: "Натив",
  video: "Видео",
}
