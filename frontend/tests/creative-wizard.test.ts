import { describe, expect, it } from "vitest"
import type { BatchItem } from "@/lib/creative-batch"
import { effectiveClickUrl, effectiveName, itemToBannerDraft, planWizardBatch } from "@/lib/creative-wizard"

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
