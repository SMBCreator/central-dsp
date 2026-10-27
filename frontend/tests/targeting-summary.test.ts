import { describe, expect, it } from "vitest"
import type { Targeting } from "@/lib/types"
import { inventorySummary } from "@/lib/targeting-context"
import {
  ADVANCED_EMPTY,
  INVENTORY_ADVANCED_KEYS,
  REGULATORY_EMPTY,
  REGULATORY_KEYS,
  SLOT_EMPTY,
  SLOT_KEYS,
  advancedInventorySummary,
  hasAdvancedInventory,
  hasRegulatorySettings,
  hasSlotSettings,
  regulatorySummary,
  slotSummary,
} from "@/lib/targeting-summary"

const SAMPLE: Record<string, unknown> = {
  banner_positions: [1], interstitial_only: true, exclude_interstitial: true, rewarded_only: true,
  secure_only: true, video_skippable_only: true, video_min_duration: 5, deal_ids: ["d1"],
  video_placements: [1], video_protocols: [2], viewability_min: 50,
  block_coppa: true, block_gdpr: true, skip_dnt: true, schain_required: true,
  domain_allowlist: ["a.ru"], domain_blocklist: ["b.ru"], app_bundles_allow: ["com.a"],
  app_bundles_block: ["com.b"], publisher_allow: ["p1"], publisher_block: ["p2"],
}

describe("пустой таргетинг", () => {
  it("нигде нет настроек", () => {
    expect(hasSlotSettings({})).toBe(false)
    expect(hasRegulatorySettings({})).toBe(false)
    expect(hasAdvancedInventory({})).toBe(false)
  })
  it("пустые массивы, false и 0 не считаются настройкой", () => {
    const t: Targeting = {
      banner_positions: [], deal_ids: [], secure_only: false, viewability_min: 0, video_min_duration: 0,
      block_coppa: false, domain_allowlist: [], publisher_block: [],
    }
    expect(hasSlotSettings(t)).toBe(false)
    expect(hasRegulatorySettings(t)).toBe(false)
    expect(hasAdvancedInventory(t)).toBe(false)
    expect(slotSummary(t)).toBe(SLOT_EMPTY)
    expect(regulatorySummary(t)).toBe(REGULATORY_EMPTY)
    expect(advancedInventorySummary(t)).toBe(ADVANCED_EMPTY)
  })
})

describe("слот", () => {
  it.each(SLOT_KEYS)("поле %s включает «есть настройки» и попадает в сводку", (key) => {
    const t = { [key]: SAMPLE[key] } as Targeting
    expect(hasSlotSettings(t)).toBe(true)
    expect(slotSummary(t)).not.toBe(SLOT_EMPTY)
  })
  it("HTTPS в сводке", () => {
    expect(slotSummary({ secure_only: true })).toBe("HTTPS")
  })
  it("длительность видео видна в сводке", () => {
    expect(slotSummary({ video_min_duration: 15 })).toBe("видео ≥15 с")
  })
  it("поля регуляторики и инвентаря не влияют на слот", () => {
    expect(hasSlotSettings({ block_gdpr: true, domain_allowlist: ["a.ru"] })).toBe(false)
  })
})

describe("регуляторика", () => {
  it.each(REGULATORY_KEYS)("поле %s включает «есть настройки»", (key) => {
    const t = { [key]: SAMPLE[key] } as Targeting
    expect(hasRegulatorySettings(t)).toBe(true)
    expect(regulatorySummary(t)).not.toBe(REGULATORY_EMPTY)
  })
  it("schain_required", () => {
    expect(regulatorySummary({ schain_required: true })).toBe("только с schain")
  })
  it("слот не влияет", () => {
    expect(hasRegulatorySettings({ secure_only: true })).toBe(false)
  })
})

describe("«Дополнительно»", () => {
  it.each(INVENTORY_ADVANCED_KEYS)("поле %s включает «есть настройки»", (key) => {
    expect(hasAdvancedInventory({ [key]: SAMPLE[key] } as Targeting)).toBe(true)
  })
  it("счётчики суммируют белый и чёрный списки в порядке домены, бандлы, паблишеры", () => {
    expect(advancedInventorySummary(SAMPLE as Targeting)).toBe("домены×2 · бандлы×2 · паблишеры×2")
    expect(advancedInventorySummary({ publisher_allow: ["p1", "p2", "p3"] })).toBe("паблишеры×3")
  })
  it("поля, оставшиеся вне группы, не считаются", () => {
    expect(hasAdvancedInventory({ ssp_allowlist: ["x"], iab_categories: ["IAB1"], keywords_allow: ["a"], traffic_types: ["app"] })).toBe(false)
  })
})

describe("inventorySummary: бандлы и паблишеры", () => {
  it("добавляет счётчики после доменов", () => {
    const t: Targeting = {
      domain_allowlist: ["a.ru"], app_bundles_allow: ["com.a"], app_bundles_block: ["com.b"], publisher_block: ["p"],
    }
    expect(inventorySummary(t)).toBe("allow×1 · бандлы×2 · паблишеры×1")
  })
  it("пустое по-прежнему «Весь инвентарь»", () => {
    expect(inventorySummary({ app_bundles_allow: [], publisher_allow: [] })).toBe("Весь инвентарь")
  })
})
