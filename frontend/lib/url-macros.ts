/**
 * Макросы ${CDSP_…} в ссылке перехода и пикселях: чистая логика без DOM.
 * Каталог — GET /creatives/macros (запасной — url-macros-catalog.ts), UI —
 * components/macro-picker.tsx, подстановка на бидере.
 */
import { FALLBACK_MACROS, type MacroEntry } from "@/lib/url-macros-catalog"

export type { MacroEntry, MacroGroup } from "@/lib/url-macros-catalog"
export { FALLBACK_MACROS, MACRO_GROUPS } from "@/lib/url-macros-catalog"

export const MACRO_API_PATH = "/creatives/macros"

export interface MacroTemplate {
  key: string
  label: string
  /** Ключи каталога: из каждого в URL добавляется параметр `<param>=${CDSP_<KEY>}`. */
  keys: string[]
  hint: string
}

export const MACRO_TEMPLATES: MacroTemplate[] = [
  {
    key: "inventory",
    label: "Площадка и креатив",
    keys: ["SITE_ID", "PLACEMENT_ID", "CREATIVE_ID", "CAMPAIGN_ID"],
    hint: "site_id, placement_id, creative_id, campaign_id",
  },
  { key: "geo", label: "Гео", keys: ["COUNTRY", "REGION", "CITY"], hint: "country, region, city" },
  { key: "device", label: "Устройство", keys: ["DEVICE_TYPE", "OS", "BROWSER"], hint: "device, os, browser" },
  { key: "pixel", label: "Для пикселя", keys: ["CACHEBUSTER", "TIMESTAMP", "BID_ID"], hint: "cb, ts, bid" },
]

/** Адрес, к которому можно дописывать параметры: схема http(s) и хост. */
export function canAppend(url: string): boolean {
  return /^https?:\/\/[^\s/?#]/i.test(url.trim())
}

function splitFragment(url: string): { base: string; fragment: string } {
  const i = url.indexOf("#")
  return i === -1 ? { base: url, fragment: "" } : { base: url.slice(0, i), fragment: url.slice(i) }
}

/** Есть ли в строке запроса параметр с таким именем (значение не важно). */
export function hasParam(url: string, param: string): boolean {
  const { base } = splitFragment(url)
  const q = base.indexOf("?")
  if (q === -1) return false
  return base
    .slice(q + 1)
    .split("&")
    .some((pair) => pair.split("=")[0] === param)
}

/**
 * Добавляет `<param>=<macro>` в конец строки запроса (перед #фрагментом).
 * Не меняет адрес, если он не http(s) или параметр уже есть.
 */
export function appendParam(url: string, param: string, macro: string): string {
  const trimmed = url.trim()
  if (!canAppend(trimmed) || hasParam(trimmed, param)) return url
  const { base, fragment } = splitFragment(trimmed)
  const sep = base.endsWith("?") || base.endsWith("&") ? "" : base.includes("?") ? "&" : "?"
  return `${base}${sep}${param}=${macro}${fragment}`
}

/** Добавляет несколько параметров каталога по их ключам; неизвестные ключи пропускает. */
export function appendParams(url: string, keys: string[], catalog: MacroEntry[] = FALLBACK_MACROS): string {
  let out = url
  for (const k of keys) {
    const e = catalog.find((m) => m.key === k)
    if (e) out = appendParam(out, e.param, e.macro)
  }
  return out
}

/** Вставляет только макрос в позицию курсора; возвращает новое значение и позицию курсора после вставки. */
export function insertAtCaret(value: string, caret: number | null | undefined, macro: string, selectionEnd?: number | null): { value: string; caret: number } {
  const start = Math.min(Math.max(caret ?? value.length, 0), value.length)
  const end = Math.min(Math.max(selectionEnd ?? start, start), value.length)
  return { value: value.slice(0, start) + macro + value.slice(end), caret: start + macro.length }
}

const TOKEN_RE = /\$\{CDSP_([A-Z0-9_]+)\}/g
const ANY_TOKEN_RE = /\$\{CDSP_[^}]*\}/gi

/** Имена макросов вида ${CDSP_NAME} в порядке появления, без повторов (каталог не проверяется). */
export function findMacros(url: string): string[] {
  const seen = new Set<string>()
  for (const m of url.matchAll(TOKEN_RE)) seen.add(m[1])
  return [...seen]
}

function distance(a: string, b: string): number {
  const prev = Array.from({ length: b.length + 1 }, (_, j) => j)
  for (let i = 1; i <= a.length; i++) {
    let diag = prev[0]
    prev[0] = i
    for (let j = 1; j <= b.length; j++) {
      const tmp = prev[j]
      prev[j] = Math.min(prev[j] + 1, prev[j - 1] + 1, diag + (a[i - 1] === b[j - 1] ? 0 : 1))
      diag = tmp
    }
  }
  return prev[b.length]
}

/** Ближайший ключ каталога для опечатки (регистр не важен) или null. */
export function suggestMacro(name: string, catalog: MacroEntry[] = FALLBACK_MACROS): string | null {
  const up = name.toUpperCase()
  let best: { key: string; d: number } | null = null
  for (const e of catalog) {
    const d = distance(up, e.key)
    if (!best || d < best.d) best = { key: e.key, d }
  }
  return best && best.d <= Math.max(2, Math.floor(up.length / 3)) ? best.key : null
}

export interface UnknownMacro {
  token: string
  suggestion: string | null
}

/** Токены ${CDSP_…}, которых нет в каталоге (опечатка, другой регистр), без повторов. */
export function unknownMacros(url: string, catalog: MacroEntry[] = FALLBACK_MACROS): UnknownMacro[] {
  const keys = new Set(catalog.map((e) => e.key))
  const out: UnknownMacro[] = []
  for (const tok of url.match(ANY_TOKEN_RE) ?? []) {
    const name = tok.slice("${CDSP_".length, -1)
    if (tok.startsWith("${CDSP_") && keys.has(name)) continue
    if (out.some((u) => u.token === tok)) continue
    out.push({ token: tok, suggestion: suggestMacro(name, catalog) })
  }
  return out
}

export interface PreviewPart {
  text: string
  kind: "text" | "macro" | "unknown"
  suggestion?: string | null
}

/** Разбор ссылки на части для предпросмотра: примеры подставлены, неизвестные макросы помечены. */
export function previewParts(url: string, catalog: MacroEntry[] = FALLBACK_MACROS): PreviewPart[] {
  const byKey = new Map(catalog.map((e) => [e.key, e]))
  const parts: PreviewPart[] = []
  let last = 0
  for (const m of url.matchAll(ANY_TOKEN_RE)) {
    const idx = m.index ?? 0
    if (idx > last) parts.push({ text: url.slice(last, idx), kind: "text" })
    const tok = m[0]
    const name = tok.slice("${CDSP_".length, -1)
    const e = tok.startsWith("${CDSP_") ? byKey.get(name) : undefined
    if (e) parts.push({ text: encodeURIComponent(e.example), kind: "macro" })
    else parts.push({ text: tok, kind: "unknown", suggestion: suggestMacro(name, catalog) })
    last = idx + tok.length
  }
  if (last < url.length) parts.push({ text: url.slice(last), kind: "text" })
  return parts
}

/** Ссылка с подставленными примерами значений (как увидит получатель). */
export function previewMacros(url: string, catalog: MacroEntry[] = FALLBACK_MACROS): string {
  return previewParts(url, catalog)
    .map((p) => p.text)
    .join("")
}

/** Предупреждение для http:// пикселя (не блокирует сохранение). */
export function insecurePixelWarning(url: string): string | null {
  return /^http:\/\//i.test(url.trim())
    ? "Пиксель по http://: на HTTPS-страницах такой пиксель может блокироваться браузером."
    : null
}
