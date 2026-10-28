import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { describe, expect, it } from "vitest"
import {
  FALLBACK_MACROS,
  MACRO_TEMPLATES,
  appendParam,
  appendParams,
  canAppend,
  findMacros,
  hasParam,
  insecurePixelWarning,
  insertAtCaret,
  previewMacros,
  previewParts,
  suggestMacro,
  unknownMacros,
} from "@/lib/url-macros"

const COUNTRY = "${CDSP_COUNTRY}"
const CITY = "${CDSP_CITY}"

describe("каталог", () => {
  it("запасной каталог совпадает с backend/url_macros.json", () => {
    const backend = JSON.parse(readFileSync(resolve(__dirname, "../../backend/url_macros.json"), "utf-8"))
    expect(FALLBACK_MACROS).toEqual(backend)
  })

  it("шаблоны ссылаются только на ключи каталога", () => {
    const keys = new Set(FALLBACK_MACROS.map((e) => e.key))
    for (const t of MACRO_TEMPLATES) for (const k of t.keys) expect(keys.has(k)).toBe(true)
  })
})

describe("appendParam", () => {
  it("U5: ? для первого параметра и & для следующих", () => {
    let u = appendParam("https://l.example/page", "country", COUNTRY)
    u = appendParam(u, "city", CITY)
    expect(u).toBe("https://l.example/page?country=${CDSP_COUNTRY}&city=${CDSP_CITY}")
  })

  it("U6: параметр добавляется перед #фрагментом", () => {
    expect(appendParam("https://l.example/page#top", "country", COUNTRY)).toBe("https://l.example/page?country=${CDSP_COUNTRY}#top")
    expect(appendParam("https://l.example/p?a=1#top", "country", COUNTRY)).toBe("https://l.example/p?a=1&country=${CDSP_COUNTRY}#top")
  })

  it("U7: повторное добавление не дублирует", () => {
    const once = appendParam("https://l.example/page", "country", COUNTRY)
    expect(appendParam(once, "country", COUNTRY)).toBe(once)
  })

  it("адрес заканчивается на ? или &", () => {
    expect(appendParam("https://l.example/p?", "country", COUNTRY)).toBe("https://l.example/p?country=${CDSP_COUNTRY}")
    expect(appendParam("https://l.example/p?a=1&", "country", COUNTRY)).toBe("https://l.example/p?a=1&country=${CDSP_COUNTRY}")
  })

  it("U4: пустое поле и не http(s) не меняются", () => {
    for (const v of ["", "   ", "l.example/page", "ftp://x.test/a", "javascript:alert(1)", "https://"]) {
      expect(appendParam(v, "country", COUNTRY)).toBe(v)
    }
    expect(canAppend("")).toBe(false)
    expect(canAppend(" https://l.example ")).toBe(true)
  })

  it("кириллица и пробелы в адресе не ломают вставку", () => {
    expect(appendParam("https://пример.рф/страница", "city", CITY)).toBe("https://пример.рф/страница?city=${CDSP_CITY}")
    expect(appendParam("  https://l.example/a b  ", "city", CITY)).toBe("https://l.example/a b?city=${CDSP_CITY}")
  })

  it("параметр с тем же именем и другим значением считается уже добавленным", () => {
    expect(appendParam("https://l.example/p?country=RU", "country", COUNTRY)).toBe("https://l.example/p?country=RU")
  })
})

describe("hasParam", () => {
  it("ищет только по имени в строке запроса", () => {
    expect(hasParam("https://x.test/?a=1&country=${CDSP_COUNTRY}", "country")).toBe(true)
    expect(hasParam("https://x.test/?a=1", "country")).toBe(false)
    expect(hasParam("https://x.test/country=1", "country")).toBe(false)
    expect(hasParam("https://x.test/?a=1#country=2", "country")).toBe(false)
    expect(hasParam("https://x.test/?mycountry=1", "country")).toBe(false)
    expect(hasParam("https://x.test/?country", "country")).toBe(true)
  })
})

describe("appendParams (шаблоны)", () => {
  it("U9: «Для пикселя» добавляет cb, ts, bid", () => {
    const t = MACRO_TEMPLATES.find((x) => x.key === "pixel")!
    expect(appendParams("https://px.example/i", t.keys)).toBe(
      "https://px.example/i?cb=${CDSP_CACHEBUSTER}&ts=${CDSP_TIMESTAMP}&bid=${CDSP_BID_ID}",
    )
  })

  it("уже имеющиеся параметры шаблона пропускаются", () => {
    const t = MACRO_TEMPLATES.find((x) => x.key === "geo")!
    const u = appendParams("https://l.example/?city=Москва", t.keys)
    expect(u).toBe("https://l.example/?city=Москва&country=${CDSP_COUNTRY}&region=${CDSP_REGION}")
  })

  it("неизвестные ключи пропускаются", () => {
    expect(appendParams("https://l.example/", ["NOPE"])).toBe("https://l.example/")
  })
})

describe("insertAtCaret", () => {
  it("вставляет в позицию курсора и сдвигает курсор", () => {
    expect(insertAtCaret("https://x.test/?a=", 18, COUNTRY)).toEqual({ value: "https://x.test/?a=${CDSP_COUNTRY}", caret: 18 + COUNTRY.length })
    expect(insertAtCaret("abcd", 2, "X")).toEqual({ value: "abXcd", caret: 3 })
  })

  it("курсор вне границ и пустой — в конец или начало", () => {
    expect(insertAtCaret("ab", 99, "X").value).toBe("abX")
    expect(insertAtCaret("ab", -5, "X").value).toBe("Xab")
    expect(insertAtCaret("ab", null, "X").value).toBe("abX")
  })

  it("заменяет выделенный фрагмент", () => {
    expect(insertAtCaret("abcd", 1, "X", 3)).toEqual({ value: "aXd", caret: 2 })
  })
})

describe("findMacros / unknownMacros", () => {
  it("findMacros: порядок появления без повторов", () => {
    expect(findMacros("https://x/?a=${CDSP_CITY}&b=${CDSP_COUNTRY}&c=${CDSP_CITY}")).toEqual(["CITY", "COUNTRY"])
    expect(findMacros("https://x/?a=${AUCTION_PRICE}")).toEqual([])
  })

  it("U8: опечатка и другой регистр — неизвестные, с подсказкой", () => {
    const bad = unknownMacros("https://x/?c=${CDSP_COUNTY}&d=${cdsp_city}")
    expect(bad.map((b) => b.token)).toEqual(["${CDSP_COUNTY}", "${cdsp_city}"])
    expect(bad[0].suggestion).toBe("COUNTRY")
    expect(bad[1].suggestion).toBe("CITY")
  })

  it("известные макросы и макросы SSP не считаются неизвестными", () => {
    expect(unknownMacros("https://x/?a=${CDSP_CITY}&p=${AUCTION_PRICE}")).toEqual([])
  })

  it("дубликаты токена сворачиваются; далёкая опечатка без подсказки", () => {
    const bad = unknownMacros("${CDSP_ZZZZZZZZZZ} ${CDSP_ZZZZZZZZZZ}")
    expect(bad).toEqual([{ token: "${CDSP_ZZZZZZZZZZ}", suggestion: null }])
    expect(suggestMacro("COUNTRY")).toBe("COUNTRY")
  })
})

describe("previewMacros / previewParts", () => {
  it("подставляет закодированные примеры", () => {
    const city = FALLBACK_MACROS.find((e) => e.key === "CITY")!
    expect(previewMacros("https://x/?city=${CDSP_CITY}")).toBe(`https://x/?city=${encodeURIComponent(city.example)}`)
  })

  it("неизвестные токены остаются как есть и помечены", () => {
    const parts = previewParts("https://x/?c=${CDSP_COUNTY}&p=${AUCTION_PRICE}")
    expect(parts.filter((p) => p.kind === "unknown")).toEqual([{ text: "${CDSP_COUNTY}", kind: "unknown", suggestion: "COUNTRY" }])
    expect(previewMacros("https://x/?c=${CDSP_COUNTY}&p=${AUCTION_PRICE}")).toBe("https://x/?c=${CDSP_COUNTY}&p=${AUCTION_PRICE}")
  })

  it("использует переданный каталог", () => {
    const cat = [{ ...FALLBACK_MACROS[0], key: "CITY", example: "Казань" }]
    expect(previewMacros("${CDSP_CITY}", cat)).toBe(encodeURIComponent("Казань"))
  })

  it("пустая строка и строка без макросов", () => {
    expect(previewMacros("")).toBe("")
    expect(previewParts("https://x/")).toEqual([{ text: "https://x/", kind: "text" }])
  })
})

describe("insecurePixelWarning", () => {
  it("U10: предупреждает только про http://", () => {
    expect(insecurePixelWarning("http://px.test/i")).toContain("http://")
    expect(insecurePixelWarning("https://px.test/i")).toBeNull()
    expect(insecurePixelWarning("")).toBeNull()
  })
})
