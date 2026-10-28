// @vitest-environment happy-dom
/**
 * ТЗ п.4: в модалке с инлайн-onClose ввод во второе поле не должен возвращать
 * фокус в первое (ререндер родителя на каждый символ).
 */
import { act, createElement, useState } from "react"
import { createRoot, type Root } from "react-dom/client"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { Modal } from "@/components/ui"

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

function Harness() {
  const [open, setOpen] = useState(false)
  const [a, setA] = useState("")
  const [b, setB] = useState("")
  return createElement(
    "div",
    null,
    createElement("button", { id: "opener", onClick: () => setOpen(true) }, "open"),
    createElement(Modal, {
      open,
      // инлайн-onClose: новая функция на каждый рендер — так пишут почти все страницы
      onClose: () => setOpen(false),
      title: "Тест",
      children: [
        createElement("input", { key: "a", id: "first", value: a, onChange: (e: { target: { value: string } }) => setA(e.target.value) }),
        createElement("input", { key: "b", id: "second", value: b, onChange: (e: { target: { value: string } }) => setB(e.target.value) }),
        createElement("button", { key: "s", id: "save", type: "button" }, "Сохранить"),
      ],
    }),
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

function press(key: string, shiftKey = false) {
  act(() => {
    document.dispatchEvent(new KeyboardEvent("keydown", { key, shiftKey, bubbles: true }))
  })
}

function openModal() {
  const opener = byId<HTMLButtonElement>("opener")
  opener.focus()
  act(() => opener.click())
}

describe("Modal + useFocusTrap", () => {
  it("фокус остаётся во втором поле при вводе с ререндером родителя", () => {
    act(() => root.render(createElement(Harness)))
    openModal()
    expect(document.activeElement?.id).toBe("first")

    const second = byId<HTMLInputElement>("second")
    act(() => second.focus())
    typeInto(second, "abc")

    expect(second.value).toBe("abc")
    expect(byId<HTMLInputElement>("first").value).toBe("")
    expect(document.activeElement).toBe(second)
  })

  it("Tab/Shift+Tab циклятся, Escape закрывает и возвращает фокус на инициатор", () => {
    act(() => root.render(createElement(Harness)))
    openModal()

    act(() => byId<HTMLButtonElement>("save").focus())
    press("Tab")
    // первый focusable в диалоге — кнопка «Закрыть» в шапке
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Закрыть")
    press("Tab", true)
    expect(document.activeElement?.id).toBe("save")

    press("Escape")
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    expect(document.activeElement?.id).toBe("opener")
  })
})
