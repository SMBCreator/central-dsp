import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
import { Disclosure } from "@/components/disclosure"

const render = (props: Partial<Omit<Parameters<typeof Disclosure>[0], "children">> = {}) =>
  renderToStaticMarkup(
    createElement(Disclosure, {
      title: "Рекламный слот",
      summary: "HTTPS",
      ...props,
      children: createElement("input", { defaultValue: "a.ru" }),
    }),
  )

describe("Disclosure", () => {
  it("свёрнут по умолчанию, но содержимое остаётся в разметке (не размонтируется)", () => {
    const html = render()
    expect(html).toContain('aria-expanded="false"')
    expect(html).toMatch(/<div id="[^"]+" hidden=""/)
    expect(html).toContain('value="a.ru"')
  })
  it("кнопка-заголовок: type=button и связь с телом через aria-controls", () => {
    const html = render()
    expect(html).toContain('type="button"')
    const controls = html.match(/aria-controls="([^"]+)"/)?.[1]
    expect(controls).toBeTruthy()
    expect(html).toContain(`id="${controls}"`)
  })
  it("defaultOpen раскрывает блок", () => {
    const html = render({ defaultOpen: true })
    expect(html).toContain('aria-expanded="true"')
    expect(html).not.toMatch(/<div id="[^"]+" hidden/)
  })
  it("сводка выделяется акцентом только при настройках", () => {
    expect(render({ accent: true })).toContain("text-warning")
    expect(render({ accent: false })).not.toContain("text-warning")
  })
  it("кнопки действий лежат вне кнопки-заголовка", () => {
    const html = render({ actions: createElement("button", { type: "button" }, "Сбросить") })
    expect(html.match(/<button/g)?.length).toBe(2)
    expect(html).not.toMatch(/<button[^>]*>(?:(?!<\/button>).)*<button/s)
  })
})
