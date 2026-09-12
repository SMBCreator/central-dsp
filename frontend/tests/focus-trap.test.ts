import { describe, expect, it } from "vitest"
import { nextTabTarget } from "@/lib/focus-trap"

describe("nextTabTarget (F-01: Tab циклится внутри dialog/drawer)", () => {
  it("пустой контейнер — ничего не делаем", () => {
    expect(nextTabTarget(0, -1, false)).toBeNull()
    expect(nextTabTarget(0, 0, true)).toBeNull()
  })

  it("фокус вне списка (на самом dialog) — Tab на первый, Shift+Tab на последний", () => {
    expect(nextTabTarget(3, -1, false)).toBe(0)
    expect(nextTabTarget(3, -1, true)).toBe(2)
  })

  it("Tab с последнего — на первый; Shift+Tab с первого — на последний", () => {
    expect(nextTabTarget(3, 2, false)).toBe(0)
    expect(nextTabTarget(3, 0, true)).toBe(2)
  })

  it("внутри списка — браузерное поведение (null)", () => {
    expect(nextTabTarget(3, 0, false)).toBeNull()
    expect(nextTabTarget(3, 1, false)).toBeNull()
    expect(nextTabTarget(3, 1, true)).toBeNull()
    expect(nextTabTarget(3, 2, true)).toBeNull()
  })

  it("единственный элемент — Tab и Shift+Tab остаются на нём", () => {
    expect(nextTabTarget(1, 0, false)).toBe(0)
    expect(nextTabTarget(1, 0, true)).toBe(0)
  })
})
