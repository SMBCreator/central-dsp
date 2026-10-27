// @vitest-environment happy-dom
/**
 * ТЗ п.3: все списки рекламодателей читают один ключ кэша; после создания
 * refreshAdvertisers() обновляет любой смонтированный список без перезагрузки.
 */
import { readdirSync, readFileSync, statSync } from "node:fs"
import { join } from "node:path"
import { act, createElement } from "react"
import { createRoot } from "react-dom/client"
import { describe, expect, it, vi } from "vitest"

const rows: { id: string; name: string; status: string }[] = [{ id: "a1", name: "Первый", status: "active" }]

vi.mock("@/lib/api", () => ({
  fetcher: vi.fn(async () => rows.map((r) => ({ ...r }))),
}))

const { ADVERTISERS_KEY, advertiserOptionLabel, refreshAdvertisers, useAdvertisers } = await import("@/lib/advertisers")

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function List() {
  const { data } = useAdvertisers()
  return createElement("ul", null, (data || []).map((a) => createElement("li", { key: a.id }, a.name)))
}

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })

describe("useAdvertisers", () => {
  it("новый рекламодатель появляется в уже открытом списке после refreshAdvertisers()", async () => {
    const host = document.createElement("div")
    document.body.appendChild(host)
    const root = createRoot(host)
    await act(async () => root.render(createElement(List)))
    await flush()
    expect(host.textContent).toBe("Первый")

    rows.push({ id: "a2", name: "Второй", status: "paused" })
    await act(async () => { await refreshAdvertisers() })
    await flush()
    expect(host.textContent).toBe("ПервыйВторой")

    act(() => root.unmount())
    host.remove()
  })

  it("неактивные помечаются, но не скрываются", () => {
    expect(advertiserOptionLabel({ name: "X", status: "active" })).toBe("X")
    expect(advertiserOptionLabel({ name: "X", status: "paused" })).toBe("X (приостановлен)")
    expect(advertiserOptionLabel({ name: "X", status: "blocked" })).toBe("X (заблокирован)")
  })

  it("никто не читает /advertisers в обход общего хука", () => {
    const root = join(__dirname, "..")
    const offenders: string[] = []
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        if (name === "node_modules" || name.startsWith(".")) continue
        const p = join(dir, name)
        if (statSync(p).isDirectory()) walk(p)
        else if (/\.(tsx?|jsx?)$/.test(name) && !p.endsWith(join("lib", "advertisers.ts"))) {
          if (/useSWR[^(]*\(\s*["'`]\/advertisers["'`]/.test(readFileSync(p, "utf8"))) offenders.push(p)
        }
      }
    }
    for (const d of ["app", "components", "lib"]) walk(join(root, d))
    expect(offenders).toEqual([])
    expect(ADVERTISERS_KEY).toBe("/advertisers")
  })
})
