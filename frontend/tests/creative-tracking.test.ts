// @vitest-environment happy-dom
/** Фаза 3: значки пикселей, блок «Трекинг» (V1, V2, V7). */
import { act, createElement } from "react"
import { createRoot, type Root } from "react-dom/client"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { PixelBadges, TrackingBlock } from "@/components/creative-tracking"

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

const render = (el: Parameters<Root["render"]>[0]) => act(() => root.render(el))

describe("PixelBadges", () => {
  it("активны, если заданы, адрес в подсказке", () => {
    render(createElement(PixelBadges, { imp: "https://px.test/i?c=${CDSP_COUNTRY}", click: "https://px.test/c" }))
    const labels = [...host.querySelectorAll("[aria-label]")].map((e) => e.getAttribute("aria-label"))
    expect(labels).toEqual(["Пиксель показ: задан", "Пиксель клик: задан"])
    expect(host.querySelectorAll("[role=tooltip]")).toHaveLength(2)
    expect(host.textContent).toContain("https://px.test/i?c=${CDSP_COUNTRY}")
  })

  it("серые и без подсказки, если не заданы", () => {
    render(createElement(PixelBadges, { imp: null, click: "" }))
    const labels = [...host.querySelectorAll("[aria-label]")].map((e) => e.getAttribute("aria-label"))
    expect(labels).toEqual(["Пиксель показ: не задан", "Пиксель клик: не задан"])
    expect(host.querySelectorAll("[role=tooltip]")).toHaveLength(0)
  })
})

describe("TrackingBlock", () => {
  it("показывает «не заданы», когда пикселей нет", () => {
    render(createElement(TrackingBlock, { creative: { click_url: "https://site.test/" } }))
    expect(host.textContent).toContain("Пиксели не заданы.")
    expect(host.querySelectorAll("button")).toHaveLength(1)
  })

  it("подсвечивает макросы и предупреждает про http:// и неизвестные макросы", () => {
    render(
      createElement(TrackingBlock, {
        creative: {
          click_url: "https://site.test/?c=${CDSP_COUNTRY}",
          imp_tracker_url: "http://px.test/i?c=${CDSP_COUNTY}",
          click_tracker_url: "https://px.test/c?cb=${CDSP_CACHEBUSTER}",
        },
      }),
    )
    const marks = [...host.querySelectorAll("mark")].map((m) => m.textContent)
    expect(marks).toEqual(["${CDSP_COUNTRY}", "${CDSP_COUNTY}", "${CDSP_CACHEBUSTER}"])
    expect(host.textContent).toContain("Неизвестные макросы")
    expect(host.textContent).toContain("${CDSP_COUNTRY}")
    expect(host.textContent).toContain("http://")
    expect(host.querySelectorAll("button")).toHaveLength(3)
  })
})
