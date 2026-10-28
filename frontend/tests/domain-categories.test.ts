import { describe, expect, it } from "vitest"
import {
  canApplyImport,
  codesError,
  importSummary,
  MAX_MANUAL_CODES,
  parseCodesInput,
  sourceLabel,
} from "@/lib/domain-categories"

describe("parseCodesInput", () => {
  it("разбивает по запятым, пробелам и переводам строк, приводит к верхнему регистру, убирает дубли", () => {
    expect(parseCodesInput("iab1, IAB1-2\niab1  iab3;").codes).toEqual(["IAB1", "IAB1-2", "IAB3"])
  })

  it("собирает недопустимые значения без дублей", () => {
    const r = parseCodesInput("IAB1 foo FOO IAB-1 IAB1-")
    expect(r.codes).toEqual(["IAB1"])
    expect(r.invalid).toEqual(["FOO", "IAB-1", "IAB1-"])
  })

  it("пустая строка — пустой результат", () => {
    expect(parseCodesInput("  ,, ")).toEqual({ codes: [], invalid: [] })
  })
})

describe("codesError", () => {
  it("ok", () => expect(codesError("IAB1, IAB2-3")).toBeNull())
  it("пусто", () => expect(codesError("")).toMatch(/хотя бы один/))
  it("мусор", () => expect(codesError("IAB1 xyz")).toMatch(/XYZ/))
  it("слишком много", () => {
    const many = Array.from({ length: MAX_MANUAL_CODES + 1 }, (_, i) => `IAB${i + 1}`).join(",")
    expect(codesError(many)).toMatch(/Не больше/)
  })
})

describe("импорт", () => {
  it("canApplyImport: нужны строки и ноль ошибок", () => {
    expect(canApplyImport(null)).toBe(false)
    expect(canApplyImport({ valid: 0, errors: [] })).toBe(false)
    expect(canApplyImport({ valid: 2, errors: [{ line: 3, domain: "x", error: "e" }] })).toBe(false)
    expect(canApplyImport({ valid: 2, errors: [] })).toBe(true)
  })

  it("importSummary", () => {
    expect(importSummary({ valid: 3, errors: [], applied: false })).toMatch(/готово к применению: 3/)
    expect(importSummary({ valid: 0, errors: [], applied: false })).toMatch(/нет строк/)
    expect(importSummary({ valid: 1, errors: [{ line: 2, domain: "", error: "e" }], applied: false })).toMatch(/с ошибками: 1/)
    expect(importSummary({ valid: 3, errors: [], applied: true, created: 2, updated: 1 })).toBe("Импортировано: 2 новых, 1 обновлено")
  })

  it("sourceLabel", () => {
    expect(sourceLabel("manual")).toBe("Вручную")
    expect(sourceLabel("learned")).toBe("Из трафика")
  })
})
