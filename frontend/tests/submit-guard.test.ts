// @vitest-environment happy-dom
/** ТЗ п.6: двойной клик по «Создать» не отправляет форму дважды. */
import { act, createElement } from "react"
import { createRoot, type Root } from "react-dom/client"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { useSubmitGuard } from "@/lib/use-submit-guard"

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

describe("useSubmitGuard", () => {
  it("второй вызов во время отправки игнорируется, после завершения — снова доступен", async () => {
    let calls = 0
    let release: () => void = () => {}
    function Harness() {
      const { submitting, guard } = useSubmitGuard()
      const submit = guard(async () => {
        calls += 1
        await new Promise<void>((r) => (release = r))
      })
      return createElement("button", { id: "b", onClick: submit, "data-busy": String(submitting) }, "Создать")
    }
    act(() => root.render(createElement(Harness)))
    const button = host.querySelector("#b") as HTMLButtonElement

    // два клика в одном тике — быстрее ререндера
    await act(async () => {
      button.click()
      button.click()
    })
    expect(calls).toBe(1)
    expect(button.dataset.busy).toBe("true")

    await act(async () => release())
    expect(button.dataset.busy).toBe("false")

    await act(async () => button.click())
    expect(calls).toBe(2)
    await act(async () => release())
  })
})
