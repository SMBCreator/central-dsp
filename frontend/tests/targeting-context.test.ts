/**
 * Контекст v2 в редакторе таргетинга: видимость переключателей, запись keywords_match,
 * сброс секции «Инвентарь» и сводки (чистые функции, без DOM).
 */
import { describe, expect, it } from "vitest"
import type { Targeting } from "@/lib/types"
import {
  INVENTORY_KEYS,
  allowUnknownEffective,
  inventoryResetPatch,
  inventorySummary,
  keywordsAllowPatch,
  keywordsMatchValue,
  showAllowUnknown,
  showContextMode,
} from "@/lib/targeting-context"

describe("showAllowUnknown", () => {
  it("скрыт без IAB-категорий", () => {
    expect(showAllowUnknown({})).toBe(false)
    expect(showAllowUnknown({ iab_categories: [] })).toBe(false)
  })
  it("виден при категориях в режиме all и без режима", () => {
    expect(showAllowUnknown({ iab_categories: ["IAB1"] })).toBe(true)
    expect(showAllowUnknown({ iab_categories: ["IAB1"], context_mode: "all" })).toBe(true)
  })
  it("скрыт в режиме any, значение в данных не стирается", () => {
    const t: Targeting = { iab_categories: ["IAB1"], context_mode: "any", iab_allow_unknown: true }
    expect(showAllowUnknown(t)).toBe(false)
    expect(t.iab_allow_unknown).toBe(true)
    expect(allowUnknownEffective(t)).toBe(false)
  })
})

describe("showContextMode", () => {
  it("виден только когда заданы и IAB, и разрешающие слова", () => {
    expect(showContextMode({ iab_categories: ["IAB1"], keywords_allow: ["ипотек"] })).toBe(true)
    expect(showContextMode({ iab_categories: ["IAB1"] })).toBe(false)
    expect(showContextMode({ keywords_allow: ["ипотек"] })).toBe(false)
    expect(showContextMode({ iab_categories: ["IAB1"], keywords_block: ["порно"] })).toBe(false)
  })
})

describe("keywordsAllowPatch", () => {
  it("мастер: первое слово без ключа пишет word_start", () => {
    expect(keywordsAllowPatch({}, ["ипотек"], true)).toEqual({ keywords_allow: ["ипотек"], keywords_match: "word_start" })
  })
  it("мастер: явно выбранное значение не перезаписывается", () => {
    expect(keywordsAllowPatch({ keywords_match: "whole_word" }, ["банк"], true)).toEqual({ keywords_allow: ["банк"] })
  })
  it("мастер: второе слово ключ не трогает", () => {
    expect(keywordsAllowPatch({ keywords_allow: ["a"], keywords_match: "substring" }, ["a", "b"], true)).toEqual({
      keywords_allow: ["a", "b"],
    })
  })
  it("карточка существующей кампании: ключ автоматически не пишется", () => {
    expect(keywordsAllowPatch({}, ["ипотек"], false)).toEqual({ keywords_allow: ["ипотек"] })
    expect(keywordsAllowPatch({ keywords_allow: ["a"] }, ["a", "b"], false)).not.toHaveProperty("keywords_match")
  })
  it("пустой список превращается в undefined и ключ не пишется", () => {
    const patch = keywordsAllowPatch({ keywords_allow: ["a"] }, [], true)
    expect(patch.keywords_allow).toBeUndefined()
    expect(patch).not.toHaveProperty("keywords_match")
  })
})

describe("keywordsMatchValue", () => {
  it("нет ключа — legacy, неизвестное значение — тоже legacy", () => {
    expect(keywordsMatchValue({})).toBe("legacy")
    expect(keywordsMatchValue({ keywords_match: "garbage" })).toBe("legacy")
    expect(keywordsMatchValue({ keywords_match: "word_start" })).toBe("word_start")
  })
})

describe("inventoryResetPatch", () => {
  it("сбрасывает и новые ключи", () => {
    const patch = inventoryResetPatch()
    for (const k of ["iab_allow_unknown", "keywords_match", "context_mode", "keywords_allow", "iab_categories"]) {
      expect(patch).toHaveProperty(k, undefined)
    }
    expect(Object.keys(patch).sort()).toEqual([...INVENTORY_KEYS].sort())
  })
  it("применённый патч оставляет чужие секции нетронутыми", () => {
    const t: Targeting = { geo_countries: ["KZ"], iab_categories: ["IAB1"], context_mode: "any", keywords_match: "word_start" }
    const next = { ...t, ...inventoryResetPatch() }
    expect(inventorySummary(next)).toBe("Весь инвентарь")
    expect(next.geo_countries).toEqual(["KZ"])
  })
})

describe("inventorySummary", () => {
  it("пустая секция", () => {
    expect(inventorySummary({})).toBe("Весь инвентарь")
  })
  it("IAB с опцией «без тематики»", () => {
    expect(inventorySummary({ iab_categories: ["IAB1", "IAB2"] })).toBe("IAB: 2")
    expect(inventorySummary({ iab_categories: ["IAB1", "IAB2"], iab_allow_unknown: true })).toBe("IAB: 2 (+без тематики)")
  })
  it("опция без категорий и в режиме any не отображается", () => {
    expect(inventorySummary({ iab_allow_unknown: true })).toBe("Весь инвентарь")
    expect(inventorySummary({ iab_categories: ["IAB1"], iab_allow_unknown: true, context_mode: "any" })).toBe("IAB: 1")
  })
  it("слова с режимом", () => {
    expect(inventorySummary({ keywords_allow: ["a", "b", "c"], keywords_match: "word_start" })).toBe(
      "слова: 3 (по началу слова)",
    )
    expect(inventorySummary({ keywords_allow: ["a"], keywords_match: "whole_word" })).toBe("слова: 1 (целое слово)")
    expect(inventorySummary({ keywords_allow: ["a"], keywords_match: "substring" })).toBe("слова: 1 (часть слова)")
  })
  it("слова легаси-кампании без режима", () => {
    expect(inventorySummary({ keywords_allow: ["a", "b"] })).toBe("слова: 2")
  })
  it("«или» между тематикой и словами в режиме any", () => {
    const t: Targeting = { iab_categories: ["IAB1"], keywords_allow: ["a"], keywords_match: "word_start", context_mode: "any" }
    expect(inventorySummary(t)).toBe("IAB: 1 · или · слова: 1 (по началу слова)")
    expect(inventorySummary({ ...t, context_mode: "all" })).toBe("IAB: 1 · слова: 1 (по началу слова)")
  })
  it("«или» не появляется, если задано только одно из двух", () => {
    expect(inventorySummary({ iab_categories: ["IAB1"], context_mode: "any" })).toBe("IAB: 1")
    expect(inventorySummary({ keywords_allow: ["a"], context_mode: "any" })).toBe("слова: 1")
  })
  it("остальные части сводки сохранены", () => {
    const t: Targeting = {
      ssp_allowlist: ["x"], iab_block: ["IAB9"], traffic_types: ["app"], domain_allowlist: ["a.ru"], domain_blocklist: ["b.ru"],
    }
    expect(inventorySummary(t)).toBe("SSP: 1 · IAB−1 · Приложения · allow×1 · block×1")
  })
})
