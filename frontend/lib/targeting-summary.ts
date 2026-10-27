import type { Targeting } from "@/lib/types"

/** Сводки и «есть ли непустые значения» для блоков редактора таргетинга, которые
 *  можно свернуть. Чистые функции без DOM: по ним редактор решает, подсвечивать ли
 *  свёрнутый заголовок, чтобы скрытые ограничения не терялись из виду. */

export const SLOT_KEYS = [
  "banner_positions", "interstitial_only", "exclude_interstitial", "rewarded_only", "secure_only",
  "video_skippable_only", "video_min_duration", "deal_ids", "video_placements", "video_protocols",
  "viewability_min",
] as const satisfies readonly (keyof Targeting)[]

export const REGULATORY_KEYS = [
  "block_coppa", "block_gdpr", "skip_dnt", "schain_required",
] as const satisfies readonly (keyof Targeting)[]

/** Поля группы «Дополнительно» в блоке «Инвентарь». */
export const INVENTORY_ADVANCED_KEYS = [
  "domain_allowlist", "domain_blocklist", "app_bundles_allow", "app_bundles_block",
  "publisher_allow", "publisher_block",
] as const satisfies readonly (keyof Targeting)[]

function isSet(v: unknown): boolean {
  if (Array.isArray(v)) return v.length > 0
  if (typeof v === "number") return v > 0
  return !!v
}

function anySet(t: Targeting, keys: readonly (keyof Targeting)[]): boolean {
  return keys.some((k) => isSet(t[k]))
}

export function hasSlotSettings(t: Targeting): boolean {
  return anySet(t, SLOT_KEYS)
}

export function hasRegulatorySettings(t: Targeting): boolean {
  return anySet(t, REGULATORY_KEYS)
}

export function hasAdvancedInventory(t: Targeting): boolean {
  return anySet(t, INVENTORY_ADVANCED_KEYS)
}

export const SLOT_EMPTY = "Любой слот"
export const REGULATORY_EMPTY = "Без ограничений"

export function slotSummary(t: Targeting): string {
  const parts: string[] = []
  if (t.banner_positions?.length) parts.push("позиция")
  if (t.interstitial_only) parts.push("interstitial")
  if (t.exclude_interstitial) parts.push("без interstitial")
  if (t.rewarded_only) parts.push("rewarded")
  if (t.secure_only) parts.push("HTTPS")
  if (t.video_skippable_only) parts.push("skippable")
  if (t.video_min_duration && t.video_min_duration > 0) parts.push(`видео ≥${t.video_min_duration} с`)
  if (t.video_placements?.length) parts.push(`plcmt×${t.video_placements.length}`)
  if (t.video_protocols?.length) parts.push(`VAST×${t.video_protocols.length}`)
  if (t.viewability_min) parts.push(`viewability ≥${t.viewability_min}%`)
  if (t.deal_ids?.length) parts.push(`PMP×${t.deal_ids.length}`)
  return parts.join(" · ") || SLOT_EMPTY
}

export function regulatorySummary(t: Targeting): string {
  const parts: string[] = []
  if (t.block_coppa) parts.push("без COPPA")
  if (t.block_gdpr) parts.push("без GDPR")
  if (t.skip_dnt) parts.push("без DNT")
  if (t.schain_required) parts.push("только с schain")
  return parts.join(" · ") || REGULATORY_EMPTY
}

export const ADVANCED_EMPTY = "Не заданы"

/** Счётчики группы «Дополнительно»: белый и чёрный списки суммируются. */
export function advancedInventorySummary(t: Targeting): string {
  const count = (a?: unknown[], b?: unknown[]) => (a?.length ?? 0) + (b?.length ?? 0)
  const parts: string[] = []
  const domains = count(t.domain_allowlist, t.domain_blocklist)
  const bundles = count(t.app_bundles_allow, t.app_bundles_block)
  const publishers = count(t.publisher_allow, t.publisher_block)
  if (domains) parts.push(`домены×${domains}`)
  if (bundles) parts.push(`бандлы×${bundles}`)
  if (publishers) parts.push(`паблишеры×${publishers}`)
  return parts.join(" · ") || ADVANCED_EMPTY
}
