// @vitest-environment happy-dom
/**
 * ТЗ п.5: рекламодатель «за пределами РФ» — в мастере кампании нет полей
 * маркировки, российские гео не предлагаются; у 'ru' прежний набор.
 */
import { act, createElement } from "react"
import { createRoot, type Root } from "react-dom/client"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Advertiser } from "@/lib/types"

const ADVERTISERS: Advertiser[] = [
  { id: "adv-ru", display_id: 1, name: "Рос Реклама", domain: null, category: null, status: "active", balance: 0, currency: "RUB", ad_market: "ru", created_at: "" },
  { id: "adv-kz", display_id: 2, name: "Almaty Ads", domain: null, category: null, status: "active", balance: 0, currency: "RUB", ad_market: "foreign", created_at: "" },
  // старый ответ API без поля — трактуется как 'ru'
  { id: "adv-old", display_id: 3, name: "Старый", domain: null, category: null, status: "active", balance: 0, currency: "RUB", created_at: "" },
]

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }) }))
vi.mock("@/lib/advertisers", async (orig) => ({
  ...(await orig<typeof import("@/lib/advertisers")>()),
  useAdvertisers: () => ({ data: ADVERTISERS }),
}))
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  // сеть в тестах не нужна: списки групп/контрагентов пустые
  fetcher: async () => [],
  api: async () => [],
}))

import { advertiserMarket, isForeignAdvertiser, stripRuGeo, targetingOutsideRu } from "@/lib/ad-market"
import { GeoDrawer, RU_REGIONS } from "@/components/geo-panel"
import NewCampaignPage from "@/app/campaigns/new/page"

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  host = document.createElement("div")
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })
}

function selectAdvertiser(id: string) {
  const select = host.querySelector("select") as HTMLSelectElement
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!
  act(() => {
    setter.call(select, id)
    select.dispatchEvent(new Event("change", { bubbles: true }))
  })
}

describe("критерий «вне РФ» (зеркало ord.service._not_ru_only_sql)", () => {
  it("страны без РФ — вне РФ; РФ в списке или пустое гео — нет", () => {
    expect(targetingOutsideRu({ geo_countries: ["KZ", "DE"] })).toBe(true)
    expect(targetingOutsideRu({ geo_countries: ["KZ", "RU"] })).toBe(false)
    expect(targetingOutsideRu({ geo_countries: ["ru"] })).toBe(false)
    expect(targetingOutsideRu({})).toBe(false)
  })

  it("места справочника: строгий режим — да, lenient (показы без гео) — нет", () => {
    expect(targetingOutsideRu({ geo_place_ids: [990100002] })).toBe(true)
    expect(targetingOutsideRu({ geo_place_ids: [990100002], geo_unknown: "lenient" })).toBe(false)
  })

  it("stripRuGeo убирает РФ, регионы, города и lenient, оставляя зарубежные страны", () => {
    expect(
      stripRuGeo({ geo_countries: ["RU", "KZ"], geo_regions: ["Москва"], geo_cities: ["Москва"], geo_unknown: "lenient", os: ["iOS"] }),
    ).toEqual({ geo_countries: ["KZ"], geo_regions: undefined, geo_cities: undefined, geo_unknown: undefined, os: ["iOS"] })
  })

  it("рынок рекламодателя: без поля — 'ru'", () => {
    expect(advertiserMarket(ADVERTISERS[2])).toBe("ru")
    expect(isForeignAdvertiser(ADVERTISERS, "adv-kz")).toBe(true)
    expect(isForeignAdvertiser(ADVERTISERS, "adv-ru")).toBe(false)
    expect(isForeignAdvertiser(ADVERTISERS, "")).toBe(false)
  })
})

describe("пикер гео (GeoDrawer)", () => {
  const render = (foreignOnly: boolean) =>
    act(() => {
      root.render(
        createElement(GeoDrawer, { open: true, onClose: () => {}, regions: [], cities: [], countries: [], onApply: () => {}, foreignOnly }),
      )
    })

  it("'ru': регионы России в списке", () => {
    render(false)
    expect(document.body.textContent).toContain(RU_REGIONS[0])
    expect(document.body.textContent).toContain("Все регионы и города")
  })

  it("'foreign': регионов России нет, зарубежные страны доступны", () => {
    render(true)
    const text = document.body.textContent || ""
    for (const r of RU_REGIONS.slice(0, 10)) expect(text).not.toContain(r)
    expect(text).not.toContain("Все регионы и города")
    expect(text).toContain("Казахстан")
  })
})

describe("мастер «Новая кампания»", () => {
  it("'ru': шаг 0 — данные ОРД", async () => {
    act(() => root.render(createElement(NewCampaignPage)))
    await flush()
    selectAdvertiser("adv-ru")
    await flush()
    expect(host.textContent).toContain("Данные для ОРД")
    expect(host.textContent).toContain("Рекламодатель и ОРД")
  })

  it("'foreign': без полей маркировки, «Далее» доступно сразу", async () => {
    act(() => root.render(createElement(NewCampaignPage)))
    await flush()
    selectAdvertiser("adv-kz")
    await flush()
    const text = host.textContent || ""
    expect(text).not.toContain("Данные для ОРД")
    expect(text).not.toContain("Рекламодатель и ОРД")
    expect(text).not.toContain("Проверить данные")
    expect(text).toContain("маркировка (ОРД, договоры, ККТУ, erid) не требуется")
    const next = Array.from(host.querySelectorAll("button")).find((b) => b.textContent === "Далее") as HTMLButtonElement
    expect(next.disabled).toBe(false)
  })
})
