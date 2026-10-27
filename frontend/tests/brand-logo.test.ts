import { readFileSync, readdirSync } from "node:fs"
import { join } from "node:path"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
import { BrandLogo } from "@/components/brand-logo"

const root = join(__dirname, "..")

describe("BrandLogo (ТЗ п.1)", () => {
  it("рендерит чёрный вариант для светлой темы и белый для тёмной", () => {
    const html = renderToStaticMarkup(createElement(BrandLogo, { height: 24 }))
    expect(html).toContain('src="/brand/logo-black.svg"')
    expect(html).toContain('src="/brand/logo-white.svg"')
    expect(html).toMatch(/logo-black\.svg"[^>]*class="block dark:hidden"/)
    expect(html).toMatch(/logo-white\.svg"[^>]*class="hidden dark:block"/)
    expect(html).toContain('alt="Central ADV"')
    expect(html).toContain('height="24"')
    expect(html).toContain('width="128"')
  })

  it("знак — для свёрнутого меню", () => {
    const html = renderToStaticMarkup(createElement(BrandLogo, { variant: "mark", height: 24, alt: "" }))
    expect(html).toContain("/brand/mark-black.svg")
    expect(html).toContain("/brand/mark-white.svg")
  })

  it("SVG без скриптов, обработчиков и внешних ссылок", () => {
    const dir = join(root, "public", "brand")
    const files = [...readdirSync(dir).map((f) => join(dir, f)), join(root, "app", "icon.svg")]
    expect(files.length).toBeGreaterThanOrEqual(5)
    for (const f of files) {
      const svg = readFileSync(f, "utf8")
      expect(svg, f).not.toMatch(/<script|<foreignObject|<image|\son[a-z]+=|href=/i)
    }
  })
})
