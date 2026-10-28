import { describe, expect, it } from "vitest"
import {
  ERID_PLACEHOLDER,
  THUMB_H,
  THUMB_W,
  advertiserFromCreative,
  advertiserLines,
  popupPosition,
  previewSubtitle,
  thumbAriaLabel,
  thumbBox,
} from "@/lib/creative-badge"

describe("advertiserLines", () => {
  it("без данных — только заглушка erid", () => {
    expect(advertiserLines({})).toEqual([ERID_PLACEHOLDER])
  })
  it("полный набор реквизитов в фиксированном порядке", () => {
    expect(advertiserLines({ erid: "E1", name: "ООО Ромашка", inn: "7701", ogrn: "1027", address: "Москва" })).toEqual([
      "erid: E1",
      "Рекламодатель: ООО Ромашка",
      "ИНН: 7701",
      "ОГРН: 1027",
      "Адрес: Москва",
    ])
  })
  it("пустые строки и null пропускаются", () => {
    expect(advertiserLines({ erid: "", name: "X", inn: null, ogrn: "", address: undefined })).toEqual([ERID_PLACEHOLDER, "Рекламодатель: X"])
  })
})

describe("advertiserFromCreative", () => {
  it("берёт counterparty_* и erid", () => {
    const a = advertiserFromCreative({
      name: "c", type: "banner", erid: "E2",
      counterparty_name: "N", counterparty_inn: "1", counterparty_ogrn: "2", counterparty_address: "A",
    })
    expect(a).toEqual({ name: "N", inn: "1", ogrn: "2", address: "A", erid: "E2" })
  })
  it("креатив без контрагента даёт пустые реквизиты (плашка покажет заглушку)", () => {
    const a = advertiserFromCreative({ name: "c", type: "native" })
    expect(a).toEqual({ name: null, inn: null, ogrn: null, address: null, erid: null })
    expect(advertiserLines(a)).toEqual([ERID_PLACEHOLDER])
  })
})

describe("thumbBox", () => {
  it("широкий баннер вписывается по ширине", () => {
    expect(thumbBox(970, 90)).toEqual({ width: THUMB_W, height: 9 })
  })
  it("высокий баннер — по высоте", () => {
    expect(thumbBox(300, 600)).toEqual({ width: 36, height: THUMB_H })
  })
  it("маленький не увеличивается", () => {
    expect(thumbBox(50, 50)).toEqual({ width: 50, height: 50 })
  })
  it("нет размеров — полная рамка", () => {
    expect(thumbBox(null, null)).toEqual({ width: THUMB_W, height: THUMB_H })
    expect(thumbBox(0, 10)).toEqual({ width: THUMB_W, height: THUMB_H })
  })
  it("никогда не нулевой", () => {
    const b = thumbBox(10000, 1)
    expect(b.height).toBeGreaterThanOrEqual(1)
  })
})

describe("previewSubtitle / thumbAriaLabel", () => {
  it("тип · размер · кампания", () => {
    expect(previewSubtitle({ name: "n", type: "banner", width: 728, height: 90 }, "Весна")).toBe("Баннер · 728×90 · Весна")
  })
  it("части без данных пропускаются", () => {
    expect(previewSubtitle({ name: "n", type: "video" })).toBe("Видео")
  })
  it("aria-label по ТЗ", () => {
    expect(thumbAriaLabel("Лето")).toBe("Открыть креатив «Лето» в полном размере")
  })
})

describe("popupPosition", () => {
  const vp = { width: 1000, height: 800 }
  const pop = { width: 224, height: 100 }
  it("выравнивается по правому краю кнопки под ней", () => {
    expect(popupPosition({ top: 100, bottom: 120, right: 600 }, pop, vp)).toEqual({ top: 124, left: 376 })
  })
  it("не уходит за левый край", () => {
    expect(popupPosition({ top: 10, bottom: 30, right: 50 }, pop, vp).left).toBe(8)
  })
  it("не уходит за правый край", () => {
    expect(popupPosition({ top: 10, bottom: 30, right: 2000 }, pop, vp).left).toBe(1000 - 224 - 8)
  })
  it("при нехватке места снизу раскрывается вверх", () => {
    expect(popupPosition({ top: 700, bottom: 720, right: 600 }, pop, vp).top).toBe(700 - 100 - 4)
  })
})
