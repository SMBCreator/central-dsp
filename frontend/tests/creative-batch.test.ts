/**
 * ТЗ batch creatives п.6/п.8: чистая логика пакетной загрузки баннеров.
 */
import { describe, expect, it } from "vitest"
import { FALLBACK_AD_SIZES, type AdSize } from "@/lib/ad-sizes-catalog"
import {
  BATCH_MAX_FILES,
  DEFAULT_LIMITS,
  IMAGE_MAX_BYTES,
  batchReducer,
  classifyFile,
  collectFiles,
  isSubmittable,
  makeItem,
  matchSize,
  summarize,
  type BatchItem,
  type EntryLike,
} from "@/lib/creative-batch"

const SIZES = FALLBACK_AD_SIZES

describe("справочник", () => {
  it("24 уникальных размера, есть 120x600, 240x600, 300x500, 970x90, 970x250, нет 20x600", () => {
    const keys = SIZES.map((s) => s.size)
    expect(keys).toHaveLength(24)
    expect(new Set(keys).size).toBe(24)
    for (const want of ["120x600", "240x600", "300x500", "320x50", "320x100", "970x90", "970x250"]) {
      expect(keys).toContain(want)
    }
    expect(keys).not.toContain("20x600")
  })
})

describe("matchSize", () => {
  it("все 24 размера сопоставляются точно", () => {
    for (const s of SIZES) {
      const m = matchSize(s.width, s.height, SIZES)
      expect(m.match?.size).toBe(s.size)
      expect(m.nearest).toEqual([])
    }
  })

  it("301x250 → ближайший 300x250, до трёх вариантов", () => {
    const m = matchSize(301, 250, SIZES)
    expect(m.match).toBeNull()
    expect(m.nearest[0].size).toBe("300x250")
    expect(m.nearest.length).toBeLessThanOrEqual(3)
    expect(m.swapped).toBeNull()
  })

  it.each([
    [250, 300, "300x250"],
    [600, 300, "300x600"],
    [50, 320, "320x50"],
  ])("%ix%i → подсказка «перевёрнутый» %s", (w, h, want) => {
    const m = matchSize(w, h, SIZES)
    expect(m.match).toBeNull()
    expect(m.swapped?.size).toBe(want)
  })

  it("0x0 и неизвестный размер — без совпадений", () => {
    expect(matchSize(0, 0, SIZES)).toEqual({ match: null, nearest: [], swapped: null })
    expect(matchSize(null, null, SIZES)).toEqual({ match: null, nearest: [], swapped: null })
  })
})

describe("classifyFile", () => {
  const ok = { type: "image/png", size: 50_000, width: 300, height: 250 }

  it("годный файл — Готов", () => {
    const c = classifyFile(ok, DEFAULT_LIMITS, SIZES)
    expect(c.status).toBe("ready")
    expect(c.match?.size).toBe("300x250")
  })

  it("неподдерживаемый тип", () => {
    const c = classifyFile({ ...ok, type: "image/svg+xml" }, DEFAULT_LIMITS, SIZES)
    expect(c.status).toBe("error")
    expect(c.reasons[0].code).toBe("type")
    expect(c.reasons[0].message).toContain("PNG, JPG, GIF, WebP")
  })

  it("вес выше общего лимита — показывает фактический и допустимый", () => {
    const c = classifyFile({ ...ok, size: IMAGE_MAX_BYTES + 1 }, DEFAULT_LIMITS, SIZES)
    expect(c.status).toBe("error")
    const r = c.reasons.find((x) => x.code === "weight")!
    expect(r.message).toContain("2,0 МБ")
  })

  it("не читается как изображение", () => {
    const c = classifyFile({ ...ok, width: null, height: null }, DEFAULT_LIMITS, SIZES)
    expect(c.status).toBe("error")
    expect(c.reasons.some((r) => r.code === "unreadable")).toBe(true)
  })

  it("размер вне справочника — ошибка с ближайшими; перевёрнутый — с подсказкой", () => {
    const near = classifyFile({ ...ok, width: 301 }, DEFAULT_LIMITS, SIZES)
    expect(near.status).toBe("error")
    expect(near.reasons[0].message).toContain("300×250")
    const sw = classifyFile({ ...ok, width: 250, height: 300 }, DEFAULT_LIMITS, SIZES)
    expect(sw.reasons[0].message).toContain("перевёрнутый")
  })

  it("max_bytes размера — предупреждение (или ошибка в строгом режиме)", () => {
    const sizes: AdSize[] = SIZES.map((s) => (s.size === "300x250" ? { ...s, max_bytes: 40_000 } : s))
    const warn = classifyFile(ok, DEFAULT_LIMITS, sizes)
    expect(warn.status).toBe("warning")
    expect(warn.reasons[0].code).toBe("size_weight")
    const strict = classifyFile(ok, { ...DEFAULT_LIMITS, weightMode: "error" }, sizes)
    expect(strict.status).toBe("error")
  })
})

// ------------------------------------------------------------------ reducer

const mk = (id: string, w: number, h: number, extra: Partial<Parameters<typeof makeItem>[0]> = {}) =>
  makeItem({ id, fileName: `${id}.png`, size: 1000, mime: "image/png", previewUrl: null, width: w, height: h, ...extra }, SIZES)

const uploaded = (state: BatchItem[], id: string, w: number, h: number) =>
  batchReducer(state, { type: "uploadDone", id, assetUrl: `https://dsp.test/uploads/${id}.png`, width: w, height: h, sizes: SIZES })

describe("batchReducer", () => {
  it("файл с ошибкой не блокирует остальные", () => {
    let s = batchReducer([], { type: "add", items: [mk("a", 300, 250), mk("bad", 301, 250), mk("b", 728, 90)] })
    expect(s.find((i) => i.id === "bad")?.phase).toBe("local")
    s = uploaded(s, "a", 300, 250)
    s = uploaded(s, "b", 728, 90)
    const sum = summarize(s)
    expect(sum.ready).toBe(2)
    expect(sum.skipped).toBe(1)
    expect(s.filter(isSubmittable).map((i) => i.id)).toEqual(["a", "b"])
  })

  it("название по умолчанию — имя файла без расширения; правка и своя ссылка", () => {
    let s = batchReducer([], { type: "add", items: [mk("promo-autumn", 300, 250)] })
    expect(s[0].name).toBe("promo-autumn")
    s = batchReducer(s, { type: "rename", id: "promo-autumn", name: "Осень" })
    s = batchReducer(s, { type: "setClickUrl", id: "promo-autumn", clickUrl: "https://site.test/autumn" })
    expect(s[0].name).toBe("Осень")
    expect(s[0].clickUrl).toBe("https://site.test/autumn")
  })

  it("удаление", () => {
    let s = batchReducer([], { type: "add", items: [mk("a", 300, 250), mk("b", 300, 250)] })
    s = batchReducer(s, { type: "remove", id: "a" })
    expect(s.map((i) => i.id)).toEqual(["b"])
  })

  it("неудачная загрузка и повтор", () => {
    let s = batchReducer([], { type: "add", items: [mk("a", 300, 250)] })
    s = batchReducer(s, { type: "uploadStart", id: "a" })
    s = batchReducer(s, { type: "uploadFailed", id: "a", message: "Сетевая ошибка" })
    expect(s[0].phase).toBe("failed")
    expect(isSubmittable(s[0])).toBe(false)
    s = batchReducer(s, { type: "retry", id: "a" })
    expect(s[0].phase).toBe("queued")
    expect(s[0].uploadError).toBeNull()
  })

  it("сервер определил другой размер — предупреждение, используется серверный", () => {
    let s = batchReducer([], { type: "add", items: [mk("a", 300, 250)] })
    s = uploaded(s, "a", 728, 90)
    expect(s[0].width).toBe(728)
    expect(s[0].status).toBe("warning")
    expect(s[0].reasons.some((r) => r.code === "size_mismatch")).toBe(true)
    expect(s[0].matched?.size).toBe("728x90")
  })

  it("результат отправки: созданные убираются, ошибки остаются с причиной", () => {
    let s = batchReducer([], { type: "add", items: [mk("a", 300, 250), mk("b", 300, 250)] })
    s = uploaded(uploaded(s, "a", 300, 250), "b", 300, 250)
    s = batchReducer(s, {
      type: "submitted",
      results: [
        { client_id: "a", status: "created", creative: { id: "c1", name: "a" } },
        { client_id: "b", status: "error", error: { code: "size_mismatch", message: "Размер не совпадает" } },
      ],
    })
    expect(s.map((i) => i.id)).toEqual(["b"])
    expect(s[0].submitError).toBe("Размер не совпадает")
  })

  it(`лимит ${BATCH_MAX_FILES} файлов`, () => {
    const many = Array.from({ length: BATCH_MAX_FILES + 5 }, (_, i) => mk(`f${i}`, 300, 250))
    const s = batchReducer([], { type: "add", items: many })
    expect(s).toHaveLength(BATCH_MAX_FILES)
  })
})

// ------------------------------------------------------------- collectFiles

const fileEntry = (name: string, type = "image/png"): EntryLike => ({
  isFile: true,
  isDirectory: false,
  name,
  file: (ok) => ok(new File(["x"], name, { type })),
})

const dirEntry = (name: string, children: EntryLike[], chunk = 2): EntryLike => ({
  isFile: false,
  isDirectory: true,
  name,
  createReader: () => {
    let pos = 0
    return {
      readEntries: (ok) => {
        const part = children.slice(pos, pos + chunk)
        pos += chunk
        ok(part)
      },
    }
  },
})

describe("collectFiles", () => {
  it("рекурсивно обходит вложенные папки, читая порциями", async () => {
    const tree = [
      fileEntry("top.png"),
      dirEntry("banners", [fileEntry("a.png"), fileEntry("b.jpg", "image/jpeg"), dirEntry("mobile", [fileEntry("c.webp", "image/webp")])]),
    ]
    const r = await collectFiles(tree)
    expect(r.files.map((f) => f.name)).toEqual(["top.png", "a.png", "b.jpg", "c.webp"])
    expect(r.skipped).toBe(0)
    expect(r.truncated).toBe(false)
  })

  it("не-изображения и скрытые файлы внутри папки пропускаются без ошибки", async () => {
    const tree = [dirEntry("d", [fileEntry("a.png"), fileEntry("readme.txt", "text/plain"), fileEntry(".DS_Store", "")])]
    const r = await collectFiles(tree)
    expect(r.files.map((f) => f.name)).toEqual(["a.png"])
    expect(r.skipped).toBe(2)
  })

  it("файл верхнего уровня неверного типа не отбрасывается (покажется ошибкой)", async () => {
    const r = await collectFiles([fileEntry("doc.pdf", "application/pdf")])
    expect(r.files).toHaveLength(1)
  })

  it("лимит числа файлов и глубины", async () => {
    const big = dirEntry("big", Array.from({ length: 10 }, (_, i) => fileEntry(`${i}.png`)))
    const r = await collectFiles([big], { maxFiles: 4 })
    expect(r.files).toHaveLength(4)
    expect(r.truncated).toBe(true)

    const deep = dirEntry("l1", [dirEntry("l2", [dirEntry("l3", [fileEntry("deep.png")])]), fileEntry("shallow.png")])
    const d = await collectFiles([deep], { maxDepth: 2 })
    expect(d.files.map((f) => f.name)).toEqual(["shallow.png"])
    expect(d.truncated).toBe(true)
  })
})
