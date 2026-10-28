import type { Targeting } from "@/lib/types"

/** Контекстные ключи блока «Инвентарь»: тематика (IAB), слова и режим их объединения.
 *  Чистые функции без DOM: используются редактором и покрыты тестами. Правила —
 *  docs/CONTRACTS.md §1.3–1.5. */

export type KeywordsMatch = "word_start" | "whole_word" | "substring"
export type ContextMode = "all" | "any"

export const KEYWORDS_MATCH_OPTIONS: { value: KeywordsMatch; label: string; short: string }[] = [
  { value: "word_start", label: "По началу слова (рекомендуется)", short: "по началу слова" },
  { value: "whole_word", label: "Целое слово", short: "целое слово" },
  { value: "substring", label: "Часть слова (шире)", short: "часть слова" },
]

/** Пункт для кампании без ключа keywords_match: ключ при этом не пишется, пока пользователь не выберет значение. */
export const LEGACY_KEYWORDS_LABEL = "Как раньше (часть слова)"

export const CONTEXT_MODE_OPTIONS: { value: ContextMode; label: string }[] = [
  { value: "all", label: "Тематика и слова" },
  { value: "any", label: "Тематика или слова" },
]

/** Ключи, которые сбрасывает «Сбросить» в секции «Инвентарь» (все, а не только новые). */
export const INVENTORY_KEYS = [
  "ssp_allowlist", "iab_categories", "iab_block", "iab_allow_unknown", "traffic_types",
  "domain_allowlist", "domain_blocklist", "app_bundles_allow", "app_bundles_block",
  "publisher_allow", "publisher_block", "keywords_allow", "keywords_block",
  "keywords_match", "context_mode",
] as const satisfies readonly (keyof Targeting)[]

export function inventoryResetPatch(): Partial<Targeting> {
  return Object.fromEntries(INVENTORY_KEYS.map((k) => [k, undefined])) as Partial<Targeting>
}

export function hasIab(t: Targeting): boolean {
  return !!t.iab_categories?.length
}

export function hasAllowWords(t: Targeting): boolean {
  return !!t.keywords_allow?.length
}

export function isAnyMode(t: Targeting): boolean {
  return t.context_mode === "any"
}

/** Переключатель «Показывать, если площадка не передала тематику»: нужна хотя бы одна IAB-категория и режим не «или».
 *  В режиме «или» опция скрыта, а значение в данных не стирается (биддер его игнорирует). */
export function showAllowUnknown(t: Targeting): boolean {
  return hasIab(t) && !isAnyMode(t)
}

/** Переключатель «Совпадать должно»: заданы и тематика, и разрешающие слова. */
export function showContextMode(t: Targeting): boolean {
  return hasIab(t) && hasAllowWords(t)
}

/** Опция «без тематики» реально действует: включена, есть категории, режим не «или». */
export function allowUnknownEffective(t: Targeting): boolean {
  return !!t.iab_allow_unknown && showAllowUnknown(t)
}

export function keywordsMatchValue(t: Targeting): KeywordsMatch | "legacy" {
  const v = t.keywords_match
  return v === "word_start" || v === "whole_word" || v === "substring" ? v : "legacy"
}

export function keywordsMatchShort(t: Targeting): string | undefined {
  return KEYWORDS_MATCH_OPTIONS.find((o) => o.value === t.keywords_match)?.short
}

/** Изменение списка разрешающих слов. В мастере новой кампании при добавлении первого слова
 *  без ключа записывается keywords_match: "word_start"; в карточке существующей кампании ключ
 *  автоматически не пишется (легаси-поведение сохраняется до явного выбора). */
export function keywordsAllowPatch(t: Targeting, next: string[], isNewCampaign: boolean): Partial<Targeting> {
  const patch: Partial<Targeting> = { keywords_allow: next.length ? next : undefined }
  if (isNewCampaign && next.length > 0 && !t.keywords_allow?.length && !t.keywords_match) {
    patch.keywords_match = "word_start"
  }
  return patch
}

/** Сводка секции «Инвентарь». «Весь инвентарь» — единственное значение при пустой секции
 *  (по нему TargetSection прячет «Сбросить»). Тематика и слова идут рядом; при режиме
 *  «или» между ними стоит «или». */
export function inventorySummary(t: Targeting): string {
  const parts: string[] = []
  if (t.ssp_allowlist?.length) parts.push(`SSP: ${t.ssp_allowlist.length}`)
  if (hasIab(t)) parts.push(`IAB: ${t.iab_categories!.length}${allowUnknownEffective(t) ? " (+без тематики)" : ""}`)
  if (hasIab(t) && hasAllowWords(t) && isAnyMode(t)) parts.push("или")
  if (hasAllowWords(t)) {
    const mode = keywordsMatchShort(t)
    parts.push(`слова: ${t.keywords_allow!.length}${mode ? ` (${mode})` : ""}`)
  }
  if (t.iab_block?.length) parts.push(`IAB−${t.iab_block.length}`)
  if (t.traffic_types?.length) parts.push(t.traffic_types.includes("app") ? "Приложения" : "Web")
  if (t.domain_allowlist?.length) parts.push(`allow×${t.domain_allowlist.length}`)
  if (t.domain_blocklist?.length) parts.push(`block×${t.domain_blocklist.length}`)
  const bundles = (t.app_bundles_allow?.length ?? 0) + (t.app_bundles_block?.length ?? 0)
  if (bundles) parts.push(`бандлы×${bundles}`)
  const publishers = (t.publisher_allow?.length ?? 0) + (t.publisher_block?.length ?? 0)
  if (publishers) parts.push(`паблишеры×${publishers}`)
  return parts.join(" · ") || "Весь инвентарь"
}
