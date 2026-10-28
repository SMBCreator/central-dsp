import { describe, expect, it } from "vitest"
import type { BatchItem } from "@/lib/creative-batch"
import { creativeLaunchBody, effectiveClickUrl, effectiveName, itemToBannerDraft, planWizardBatch } from "@/lib/creative-wizard"
import { emptyDraft } from "@/components/creative-builder"

const LINK = "https://site.ru/offer"
const ASSET = "https://dsp.example.ru/uploads/0123456789abcdef0123456789abcdef.png"

function item(over: Partial<BatchItem> = {}): BatchItem {
  return {
    id: "f1",
    fileName: "banner-300x250.png",
    size: 12_000,
    mime: "image/png",
    previewUrl: null,
    width: 300,
    height: 250,
    clientWidth: 300,
    clientHeight: 250,
    name: "banner-300x250",
    clickUrl: "",
    impTrackerUrl: "",
    clickTrackerUrl: "",
    status: "ready",
    reasons: [],
    matched: null,
    phase: "uploaded",
    progress: 1,
    assetUrl: ASSET,
    uploadError: null,
    submitError: null,
    ...over,
  }
}

describe("itemToBannerDraft", () => {
  it("статичный баннер: размеры, абсолютный asset_url, общая ссылка", () => {
    const d = itemToBannerDraft(item(), LINK)
    expect(d.type).toBe("banner")
    expect(d.subtype).toBe("static")
    expect(d.width).toBe(300)
    expect(d.height).toBe(250)
    expect(d.asset_url).toBe(ASSET)
    expect(d.asset_url?.startsWith("https://")).toBe(true)
    expect(d.click_url).toBe(LINK)
    expect(d.name).toBe("banner-300x250")
    expect(d.html_snippet).toBeNull()
  })

  it("GIF получает подтип gif", () => {
    const d = itemToBannerDraft(item({ mime: "image/gif", fileName: "a.gif" }), LINK)
    expect(d.subtype).toBe("gif")
  })

  it("своя ссылка файла важнее общей", () => {
    const own = "https://other.ru/x"
    expect(effectiveClickUrl(item({ clickUrl: ` ${own} ` }), LINK)).toBe(own)
    expect(itemToBannerDraft(item({ clickUrl: own }), LINK).click_url).toBe(own)
  })

  it("пустое название заменяется именем файла без расширения", () => {
    expect(effectiveName(item({ name: "   " }))).toBe("banner-300x250")
    expect(itemToBannerDraft(item({ name: "Осень" }), LINK).name).toBe("Осень")
  })
})

describe("planWizardBatch", () => {
  it("готовые и ожидающие ссылку разделяются", () => {
    const a = item({ id: "a" })
    const b = item({ id: "b", clickUrl: "https://own.ru" })
    expect(planWizardBatch([a, b], LINK).ready.map((i) => i.id)).toEqual(["a", "b"])
    const plan = planWizardBatch([a, b], "")
    expect(plan.ready.map((i) => i.id)).toEqual(["b"])
    expect(plan.needLink.map((i) => i.id)).toEqual(["a"])
  })

  it("некорректная ссылка не считается готовой", () => {
    const plan = planWizardBatch([item({ id: "a" })], "site.ru")
    expect(plan.ready).toHaveLength(0)
    expect(plan.needLink).toHaveLength(1)
  })

  it("файлы с ошибкой и ещё не загруженные не попадают в план", () => {
    const bad = item({ id: "bad", status: "error", reasons: [{ code: "size", level: "error", message: "x" }] })
    const loading = item({ id: "load", phase: "uploading", assetUrl: null })
    const plan = planWizardBatch([bad, loading], LINK)
    expect(plan.ready).toHaveLength(0)
    expect(plan.needLink).toHaveLength(0)
  })
})

describe("пиксели файлов (U2)", () => {
  const shared = { imp: "https://common.test/i", click: "https://common.test/c" }

  it("общие пиксели наследуются, свои перекрывают", () => {
    const a = itemToBannerDraft(item({ id: "a" }), LINK, shared)
    expect(a.imp_tracker_url).toBe(shared.imp)
    expect(a.click_tracker_url).toBe(shared.click)
    const own = itemToBannerDraft(item({ id: "b", impTrackerUrl: " https://own.test/i ", clickTrackerUrl: "https://own.test/c" }), LINK, shared)
    expect(own.imp_tracker_url).toBe("https://own.test/i")
    expect(own.click_tracker_url).toBe("https://own.test/c")
  })

  it("можно задать только один из своих пикселей", () => {
    const d = itemToBannerDraft(item({ impTrackerUrl: "https://own.test/i" }), LINK, shared)
    expect(d.imp_tracker_url).toBe("https://own.test/i")
    expect(d.click_tracker_url).toBe(shared.click)
  })

  it("пустые значения становятся null, а не пустой строкой", () => {
    const d = itemToBannerDraft(item({ impTrackerUrl: "  " }), LINK)
    expect(d.imp_tracker_url).toBeNull()
    expect(d.click_tracker_url).toBeNull()
  })

  it("файл с некорректным пикселем не попадает в ready", () => {
    const ok = item({ id: "ok", impTrackerUrl: "https://own.test/i?cb=${CDSP_CACHEBUSTER}" })
    const bad = item({ id: "bad", clickTrackerUrl: "javascript:alert(1)" })
    const plan = planWizardBatch([ok, bad], LINK, shared)
    expect(plan.ready.map((i) => i.id)).toEqual(["ok"])
    expect(plan.badPixel.map((i) => i.id)).toEqual(["bad"])
  })

  it("некорректный общий пиксель блокирует файлы без своего", () => {
    const plan = planWizardBatch([item({ id: "a" }), item({ id: "b", impTrackerUrl: "https://own.test/i" })], LINK, { imp: "oops", click: "" })
    expect(plan.ready.map((i) => i.id)).toEqual(["b"])
    expect(plan.badPixel.map((i) => i.id)).toEqual(["a"])
  })
})

describe("creativeLaunchBody (U11)", () => {
  it("пиксели черновика доходят до тела запроса вместе с привязками", () => {
    const draft = { ...emptyDraft("banner"), name: "n", click_url: LINK, imp_tracker_url: "https://p.test/i", click_tracker_url: null }
    const body = creativeLaunchBody(draft, { campaignId: "c1", lineItemId: "l1", foreign: false, counterpartyId: "cp", kktuCode: "30.10", kktuLabel: "x" })
    expect(body).toMatchObject({ imp_tracker_url: "https://p.test/i", click_tracker_url: null, campaign_id: "c1", line_item_id: "l1", counterparty_id: "cp", kktu_code: "30.10" })
  })

  it("для иностранного рекламодателя ОРД-поля не добавляются", () => {
    const body = creativeLaunchBody(emptyDraft("banner"), { campaignId: "c1", lineItemId: "l1", foreign: true, counterpartyId: "cp" })
    expect(body).not.toHaveProperty("counterparty_id")
    expect(body.imp_tracker_url).toBeNull()
  })
})
