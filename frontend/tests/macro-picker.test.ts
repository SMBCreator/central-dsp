// @vitest-environment happy-dom
/** Панель макросов: сценарии U4–U9 на уровне компонента. */
import { act, createElement, useState } from "react"
import { createRoot, type Root } from "react-dom/client"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { MacroUrlInput, type MacroFieldKind } from "@/components/macro-picker"

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

function Harness({ initial = "", kind = "click" }: { initial?: string; kind?: MacroFieldKind }) {
  const [v, setV] = useState(initial)
  return createElement(MacroUrlInput, { id: "f", value: v, onChange: setV, kind })
}

const input = () => document.getElementById("f") as HTMLInputElement
const button = (text: string) =>
  [...host.querySelectorAll("button")].find((b) => (b.textContent || "").includes(text)) as HTMLButtonElement
const click = (text: string) => act(() => button(text).click())

function typeValue(value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!
  act(() => {
    setter.call(input(), value)
    input().dispatchEvent(new Event("input", { bubbles: true }))
  })
}

describe("MacroUrlInput", () => {
  it("панель свёрнута по умолчанию и раскрывается кнопкой (aria-expanded)", () => {
    act(() => root.render(createElement(Harness)))
    const toggle = button("Макросы")
    expect(toggle.getAttribute("aria-expanded")).toBe("false")
    expect(host.textContent).not.toContain("Готовые шаблоны")
    act(() => toggle.click())
    expect(button("Макросы").getAttribute("aria-expanded")).toBe("true")
    expect(host.textContent).toContain("Готовые шаблоны")
    expect(host.textContent).toContain("${AUCTION_PRICE}")
  })

  it("U4: в пустом поле чип ничего не подставляет и просит адрес", () => {
    act(() => root.render(createElement(Harness)))
    click("Макросы")
    click("Страна")
    expect(input().value).toBe("")
    expect(host.textContent).toContain("Сначала введите адрес https://…")
  })

  it("U5/U7: параметры добавляются через ? и &, повтор не дублирует", () => {
    act(() => root.render(createElement(Harness, { initial: "https://l.example/page" })))
    click("Макросы")
    click("Страна")
    click("Город")
    expect(input().value).toBe("https://l.example/page?country=${CDSP_COUNTRY}&city=${CDSP_CITY}")
    click("Страна")
    expect(input().value).toBe("https://l.example/page?country=${CDSP_COUNTRY}&city=${CDSP_CITY}")
    expect(button("Страна").getAttribute("aria-pressed")).toBe("true")
    expect(button("Страна").textContent).toContain("добавлен")
  })

  it("U6: параметр вставляется перед #фрагментом", () => {
    act(() => root.render(createElement(Harness, { initial: "https://l.example/page#top" })))
    click("Макросы")
    click("Страна")
    expect(input().value).toBe("https://l.example/page?country=${CDSP_COUNTRY}#top")
  })

  it("U8: неизвестный макрос подсвечен в предпросмотре с подсказкой", () => {
    act(() => root.render(createElement(Harness)))
    typeValue("https://l.example/?c=${CDSP_COUNTY}")
    const mark = host.querySelector("mark")
    expect(mark?.textContent).toBe("${CDSP_COUNTY}")
    expect(mark?.getAttribute("title")).toContain("COUNTRY")
    expect(host.textContent).toContain("Как будет выглядеть")
  })

  it("известный макрос в предпросмотре заменён примером", () => {
    act(() => root.render(createElement(Harness)))
    typeValue("https://l.example/?cb=${CDSP_CACHEBUSTER}")
    const preview = host.querySelector("[data-macro-preview]")?.textContent ?? ""
    expect(preview).not.toContain("CDSP_")
    expect(preview).toContain("https://l.example/?cb=")
    expect(host.querySelector("mark")).toBeNull()
  })

  it("U9: шаблон «Для пикселя» добавляет cb, ts, bid", () => {
    act(() => root.render(createElement(Harness, { initial: "https://px.example/i", kind: "pixel" })))
    click("Макросы")
    click("Для пикселя")
    expect(input().value).toBe("https://px.example/i?cb=${CDSP_CACHEBUSTER}&ts=${CDSP_TIMESTAMP}&bid=${CDSP_BID_ID}")
  })

  it("режим «только макрос»: вставка в позицию курсора", () => {
    act(() => root.render(createElement(Harness, { initial: "https://l.example/?a=" })))
    click("Макросы")
    const checkbox = host.querySelector<HTMLInputElement>('input[type="checkbox"]') as HTMLInputElement
    act(() => checkbox.click())
    act(() => input().setSelectionRange(input().value.length, input().value.length))
    click("Город")
    expect(input().value).toBe("https://l.example/?a=${CDSP_CITY}")
  })
})
