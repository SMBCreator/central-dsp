/**
 * ТЗ п.5: где рекламодатель планирует рекламу — в РФ ('ru') или только за
 * пределами РФ ('foreign'). Для 'foreign' маркировка (ОРД) не нужна, поэтому UI
 * скрывает её поля и российские гео; сервер проверяет то же самое (422).
 */
import type { Advertiser, Targeting } from "@/lib/types"

export type AdMarket = "ru" | "foreign"

export const AD_MARKET_OPTIONS: { value: AdMarket; label: string; hint: string }[] = [
  { value: "ru", label: "В России", hint: "Нужна маркировка: данные ОРД, договоры, ККТУ, erid" },
  { value: "foreign", label: "Только за пределами РФ", hint: "Без маркировки; российские гео недоступны" },
]

export const AD_MARKET_LABELS: Record<AdMarket, string> = {
  ru: "РФ",
  foreign: "За пределами РФ",
}

/** Старые ответы API без поля — 'ru' (как значение по умолчанию на сервере). */
export function advertiserMarket(a: Pick<Advertiser, "ad_market"> | null | undefined): AdMarket {
  return a?.ad_market === "foreign" ? "foreign" : "ru"
}

export function isForeignAdvertiser(advertisers: Advertiser[] | undefined, advertiserId: string | null | undefined): boolean {
  if (!advertiserId) return false
  return advertiserMarket((advertisers || []).find((a) => a.id === advertiserId)) === "foreign"
}

/**
 * Клиентское зеркало серверного критерия «группа не показывается в РФ»
 * (ord.service._not_ru_only_sql): непустой список стран без РФ, либо места
 * справочника в строгом режиме. Принадлежность мест к РФ сервер проверяет сам —
 * пикер для 'foreign' их просто не предлагает.
 */
export function targetingOutsideRu(t: Targeting): boolean {
  const countries = t.geo_countries || []
  if (countries.length > 0 && !countries.some((c) => c.toUpperCase() === "RU")) return true
  return (t.geo_place_ids?.length || 0) > 0 && t.geo_unknown !== "lenient"
}

/** Для 'foreign': убрать из таргетинга всё российское (смена рекламодателя в мастере). */
export function stripRuGeo(t: Targeting): Targeting {
  const countries = (t.geo_countries || []).filter((c) => c.toUpperCase() !== "RU")
  return {
    ...t,
    geo_countries: countries.length ? countries : undefined,
    geo_regions: undefined,
    geo_cities: undefined,
    geo_unknown: undefined,
  }
}
