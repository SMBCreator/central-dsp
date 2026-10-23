import { readdirSync, readFileSync, statSync } from "node:fs"
import { join } from "node:path"
import { describe, expect, it } from "vitest"

// api() сам делает JSON.stringify(options.body). Строка в body кодируется
// второй раз, и FastAPI отвечает 422 (ожидал объект, получил JSON-строку).
const ROOTS = ["app", "components"]
const CALL = /\bapi(?:<[^>]*>)?\(([^()]|\([^()]*\))*?body:\s*JSON\.stringify\(/g

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(name) ? [p] : []
  })
}

describe("api() body encoding", () => {
  it("never receives a pre-stringified body", () => {
    const offenders = ROOTS.flatMap(walk).flatMap((file) => {
      const src = readFileSync(file, "utf8")
      return [...src.matchAll(CALL)].map((m) => `${file}:${src.slice(0, m.index).split("\n").length}`)
    })
    expect(offenders).toEqual([])
  })

  it("detects the bug pattern", () => {
    const sample = 'await api("/x", { method: "POST", body: JSON.stringify({ a: 1 }) })'
    expect([...sample.matchAll(CALL)]).toHaveLength(1)
    const fetchSample = 'await fetch("/x", { method: "POST", body: JSON.stringify({ a: 1 }) })'
    expect([...fetchSample.matchAll(CALL)]).toHaveLength(0)
  })
})
