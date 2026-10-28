// @vitest-environment happy-dom
/**
 * ТЗ batch creatives п.6.4: ввод в поле названия карточки при ререндере родителя
 * (каждый символ меняет состояние пакета) не уводит фокус и не перерисовывает
 * соседние карточки.
 */
import { act, createElement, useCallback, useReducer, useState } from "react"
import { createRoot, type Root } from "react-dom/client"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { BatchItemCard } from "@/components/creative-batch-panel"
import { FALLBACK_AD_SIZES } from "@/lib/ad-sizes-catalog"
import { batchReducer, makeItem } from "@/lib/creative-batch"

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

const initial = ["first", "second"].map((id) =>
  makeItem({ id, fileName: `${id}.png`, size: 1000, mime: "image/png", previewUrl: null, width: 300, height: 250 }, FALLBACK_AD_SIZES),
)

function Harness() {
  const [items, dispatch] = useReducer(batchReducer, initial)
  // посторонний стейт родителя, меняющийся на каждый символ
  const [, setTick] = useState(0)
  const onRename = useCallback((id: string, name: string) => {
    dispatch({ type: "rename", id, name })
    setTick((t) => t + 1)
  }, [])
  const onClickUrl = useCallback((id: string, clickUrl: string) => dispatch({ type: "setClickUrl", id, clickUrl }), [])
  const onImp = useCallback((id: string, url: string) => dispatch({ type: "setImpTracker", id, url }), [])
  const onClickTracker = useCallback((id: string, url: string) => dispatch({ type: "setClickTracker", id, url }), [])
  const noop = useCallback(() => {}, [])
  return createElement(
    "ul",
    null,
    items.map((item) =>
      createElement(BatchItemCard, {
        key: item.id,
        item,
        defaultClickUrl: "https://site.test/",
        defaultImpTracker: "https://common.test/i",
        defaultClickTracker: "",
        onRename,
        onClickUrl,
        onImpTracker: onImp,
        onClickTracker,
        onRemove: noop,
        onRetry: noop,
      }),
    ),
  )
}

const byId = <T extends HTMLElement>(id: string) => document.getElementById(id) as T

function typeInto(el: HTMLInputElement, text: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!
  for (const ch of text) {
    act(() => {
      setter.call(el, el.value + ch)
      el.dispatchEvent(new Event("input", { bubbles: true }))
    })
  }
}

describe("BatchItemCard", () => {
  it("фокус остаётся в названии второй карточки при вводе", () => {
    act(() => root.render(createElement(Harness)))
    const second = byId<HTMLInputElement>("batch-name-second")
    expect(second.value).toBe("second")
    act(() => second.focus())
    typeInto(second, " осень")
    expect(byId<HTMLInputElement>("batch-name-second")).toBe(second)
    expect(second.value).toBe("second осень")
    expect(byId<HTMLInputElement>("batch-name-first").value).toBe("first")
    expect(document.activeElement).toBe(second)
  })

  it("ввод своей ссылки перехода не теряет фокус", () => {
    act(() => root.render(createElement(Harness)))
    const url = byId<HTMLInputElement>("batch-url-first")
    act(() => url.focus())
    typeInto(url, "https://a.test")
    expect(url.value).toBe("https://a.test")
    expect(document.activeElement).toBe(url)
  })

  it("U1: блок «Свои пиксели» свёрнут, после ввода в заголовке отметки; фокус сохраняется", () => {
    act(() => root.render(createElement(Harness)))
    const toggle = (id: string) =>
      document.querySelector<HTMLButtonElement>(`[data-batch-item="${id}"] button[aria-controls="batch-${id}-pixels"]`) as HTMLButtonElement
    expect(toggle("second").getAttribute("aria-expanded")).toBe("false")
    expect(document.getElementById("batch-second-imp")).toBeNull()
    act(() => toggle("second").click())
    expect(toggle("second").getAttribute("aria-expanded")).toBe("true")
    const imp = byId<HTMLInputElement>("batch-second-imp")
    expect(imp.placeholder).toBe("Общий: https://common.test/i")
    act(() => imp.focus())
    typeInto(imp, "https://own.test/i")
    expect(byId<HTMLInputElement>("batch-second-imp")).toBe(imp)
    expect(document.activeElement).toBe(imp)
    expect(toggle("second").textContent).toContain("показ")
    expect(toggle("second").textContent).not.toContain("клик")
    expect(toggle("first").getAttribute("aria-expanded")).toBe("false")
    expect(toggle("first").textContent).not.toContain("показ")
  })

  it("U10: пиксель по http:// показывает предупреждение, а мусор — ошибку", () => {
    act(() => root.render(createElement(Harness)))
    const btn = document.querySelector<HTMLButtonElement>('[data-batch-item="first"] button[aria-controls="batch-first-pixels"]') as HTMLButtonElement
    act(() => btn.click())
    const imp = byId<HTMLInputElement>("batch-first-imp")
    typeInto(imp, "http://px.test/i")
    expect(host.textContent).toContain("может блокироваться браузером")
    expect(host.textContent).not.toContain("Укажите адрес вида")
    const click = byId<HTMLInputElement>("batch-first-click")
    typeInto(click, "not a url")
    expect(host.textContent).toContain("Укажите адрес вида")
  })
})
