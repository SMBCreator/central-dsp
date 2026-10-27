/**
 * Пакетная загрузка баннеров: чистая логика без DOM (сопоставление размеров,
 * классификация файла, обход папки, состояние пакета). UI —
 * components/creative-batch-panel.tsx, сервер — POST /creatives/batch.
 */
import { formatSize, type AdSize } from "@/lib/ad-sizes-catalog"

export const BATCH_MAX_FILES = 30
export const UPLOAD_CONCURRENCY = 3
export const FOLDER_MAX_DEPTH = 5
export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"]
export const IMAGE_ACCEPT = IMAGE_TYPES.join(",")
export const IMAGE_MAX_BYTES = 2 * 1024 * 1024
/** Превышение индивидуального max_bytes размера: предупреждение (по умолчанию) или ошибка. */
export const SIZE_WEIGHT_MODE: "warning" | "error" = "warning"

const IMAGE_EXT_RE = /\.(png|jpe?g|gif|webp)$/i

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} Б`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} КБ`
  return `${(n / 1024 / 1024).toFixed(1).replace(".", ",")} МБ`
}

export function baseName(fileName: string): string {
  return fileName.replace(/\.[^.]+$/, "") || fileName
}

// ------------------------------------------------------------------ matchSize

export interface SizeMatch {
  match: AdSize | null
  /** До трёх ближайших допустимых — только если точного совпадения нет. */
  nearest: AdSize[]
  /** Есть допустимый размер с переставленными шириной и высотой. */
  swapped: AdSize | null
}

/** Та же формула, что backend/ad_sizes.py::nearest_sizes. */
function distance(w: number, h: number, s: AdSize): number {
  const ratio = Math.abs(Math.log(w / h / (s.width / s.height)))
  const area = Math.abs(Math.log((w * h) / (s.width * s.height)))
  return ratio * 2 + area
}

export function matchSize(width: number | null | undefined, height: number | null | undefined, sizes: AdSize[]): SizeMatch {
  if (!width || !height || width <= 0 || height <= 0) return { match: null, nearest: [], swapped: null }
  const match = sizes.find((s) => s.width === width && s.height === height) ?? null
  if (match) return { match, nearest: [], swapped: null }
  const swapped = sizes.find((s) => s.width === height && s.height === width) ?? null
  const nearest = [...sizes].sort((a, b) => distance(width, height, a) - distance(width, height, b)).slice(0, 3)
  return { match: null, nearest, swapped }
}

// --------------------------------------------------------------- classifyFile

export type ItemStatus = "ready" | "warning" | "error"

export interface Reason {
  code: "type" | "unreadable" | "weight" | "size" | "size_weight" | "size_mismatch" | "upload" | "server"
  level: "error" | "warning"
  message: string
}

export interface FileLimits {
  maxBytes: number
  types: string[]
  weightMode: "warning" | "error"
}

export const DEFAULT_LIMITS: FileLimits = { maxBytes: IMAGE_MAX_BYTES, types: IMAGE_TYPES, weightMode: SIZE_WEIGHT_MODE }

export interface FileFacts {
  type: string
  size: number
  width: number | null
  height: number | null
}

export interface Classification {
  status: ItemStatus
  reasons: Reason[]
  match: AdSize | null
}

export function statusOf(reasons: Reason[]): ItemStatus {
  if (reasons.some((r) => r.level === "error")) return "error"
  if (reasons.length > 0) return "warning"
  return "ready"
}

export function classifyFile(f: FileFacts, limits: FileLimits, sizes: AdSize[]): Classification {
  const reasons: Reason[] = []
  if (!limits.types.includes(f.type)) {
    reasons.push({ code: "type", level: "error", message: "Тип файла не поддерживается: допустимы PNG, JPG, GIF, WebP" })
    return { status: "error", reasons, match: null }
  }
  if (f.size > limits.maxBytes) {
    reasons.push({ code: "weight", level: "error", message: `Вес ${fmtBytes(f.size)} превышает лимит ${fmtBytes(limits.maxBytes)}` })
  }
  if (!f.width || !f.height) {
    reasons.push({ code: "unreadable", level: "error", message: "Файл не читается как изображение" })
    return { status: "error", reasons, match: null }
  }
  const m = matchSize(f.width, f.height, sizes)
  if (!m.match) {
    let message = `Размер ${f.width}×${f.height} не входит в справочник.`
    if (m.swapped) message += ` Возможно, перевёрнутый размер: нужен ${formatSize(m.swapped)}.`
    else if (m.nearest.length) message += ` Ближайшие допустимые: ${m.nearest.map(formatSize).join(", ")}.`
    reasons.push({ code: "size", level: "error", message })
  } else if (m.match.max_bytes && f.size > m.match.max_bytes) {
    reasons.push({
      code: "size_weight",
      level: limits.weightMode,
      message: `Вес ${fmtBytes(f.size)} выше рекомендованного для ${formatSize(m.match)} (${fmtBytes(m.match.max_bytes)})`,
    })
  }
  return { status: statusOf(reasons), reasons, match: m.match }
}

// ---------------------------------------------------------------- collectFiles

/** Минимальный контракт FileSystemEntry (webkitGetAsEntry) — подменяется в тестах. */
export interface EntryLike {
  isFile: boolean
  isDirectory: boolean
  name: string
  file?: (ok: (f: File) => void, err?: (e: unknown) => void) => void
  createReader?: () => { readEntries: (ok: (entries: EntryLike[]) => void, err?: (e: unknown) => void) => void }
}

export interface CollectResult {
  files: File[]
  /** Не-изображения и служебные файлы внутри папок — пропущены без ошибки. */
  skipped: number
  /** Достигнут лимит числа файлов или глубины вложенности. */
  truncated: boolean
}

function entryFile(e: EntryLike): Promise<File | null> {
  return new Promise((resolve) => {
    if (!e.file) return resolve(null)
    e.file(resolve, () => resolve(null))
  })
}

async function readAll(e: EntryLike): Promise<EntryLike[]> {
  const reader = e.createReader?.()
  if (!reader) return []
  const out: EntryLike[] = []
  // readEntries отдаёт порциями (Chrome — по 100), пустая порция = конец
  for (;;) {
    const batch = await new Promise<EntryLike[]>((resolve) => reader.readEntries(resolve, () => resolve([])))
    if (batch.length === 0) return out
    out.push(...batch)
  }
}

/**
 * Обход перетащенных файлов и папок. Файлы верхнего уровня берутся все (неверный
 * тип покажется ошибкой в карточке); внутри папок не-изображения и скрытые файлы
 * пропускаются молча. Рекурсия ограничена глубиной и общим числом файлов.
 */
export async function collectFiles(
  entries: EntryLike[],
  opts: { maxFiles?: number; maxDepth?: number } = {},
): Promise<CollectResult> {
  const maxFiles = opts.maxFiles ?? BATCH_MAX_FILES
  const maxDepth = opts.maxDepth ?? FOLDER_MAX_DEPTH
  const res: CollectResult = { files: [], skipped: 0, truncated: false }

  const walk = async (list: EntryLike[], depth: number) => {
    for (const e of list) {
      if (res.files.length >= maxFiles) {
        res.truncated = true
        return
      }
      if (e.isFile) {
        if (depth > 0 && (e.name.startsWith(".") || !IMAGE_EXT_RE.test(e.name))) {
          res.skipped++
          continue
        }
        const f = await entryFile(e)
        if (f) res.files.push(f)
      } else if (e.isDirectory) {
        if (depth >= maxDepth) {
          res.truncated = true
          continue
        }
        await walk(await readAll(e), depth + 1)
      }
    }
  }
  await walk(entries, 0)
  return res
}

// ------------------------------------------------------------- batch reducer

export type UploadPhase = "local" | "queued" | "uploading" | "uploaded" | "failed"

export interface BatchItem {
  id: string
  fileName: string
  size: number
  mime: string
  previewUrl: string | null
  /** Размер по браузеру; после загрузки — по ответу сервера. */
  width: number | null
  height: number | null
  clientWidth: number | null
  clientHeight: number | null
  name: string
  /** Пусто — используется общая ссылка перехода. */
  clickUrl: string
  status: ItemStatus
  reasons: Reason[]
  matched: AdSize | null
  phase: UploadPhase
  progress: number
  assetUrl: string | null
  uploadError: string | null
  submitError: string | null
}

export interface NewItemInput {
  id: string
  fileName: string
  size: number
  mime: string
  previewUrl: string | null
  width: number | null
  height: number | null
}

export function makeItem(input: NewItemInput, sizes: AdSize[], limits: FileLimits = DEFAULT_LIMITS): BatchItem {
  const c = classifyFile({ type: input.mime, size: input.size, width: input.width, height: input.height }, limits, sizes)
  return {
    ...input,
    clientWidth: input.width,
    clientHeight: input.height,
    name: baseName(input.fileName),
    clickUrl: "",
    status: c.status,
    reasons: c.reasons,
    matched: c.match,
    phase: c.status === "error" ? "local" : "queued",
    progress: 0,
    assetUrl: null,
    uploadError: null,
    submitError: null,
  }
}

export interface BatchResultItem {
  client_id: string
  status: "created" | "error"
  duplicate?: boolean
  creative?: { id: string; name: string; erid?: string | null; ord?: { status: string; error: string | null } }
  error?: { code: string; message: string }
}

export type BatchAction =
  | { type: "add"; items: BatchItem[] }
  | { type: "remove"; id: string }
  | { type: "rename"; id: string; name: string }
  | { type: "setClickUrl"; id: string; clickUrl: string }
  | { type: "uploadStart"; id: string }
  | { type: "uploadProgress"; id: string; progress: number }
  | { type: "uploadDone"; id: string; assetUrl: string; width: number | null; height: number | null; sizes: AdSize[]; limits?: FileLimits }
  | { type: "uploadFailed"; id: string; message: string }
  | { type: "retry"; id: string }
  | { type: "submitted"; results: BatchResultItem[] }
  | { type: "reset" }

const patchItem = (state: BatchItem[], id: string, fn: (i: BatchItem) => BatchItem) =>
  state.map((i) => (i.id === id ? fn(i) : i))

export function batchReducer(state: BatchItem[], action: BatchAction): BatchItem[] {
  switch (action.type) {
    case "add":
      return [...state, ...action.items].slice(0, BATCH_MAX_FILES)
    case "remove":
      return state.filter((i) => i.id !== action.id)
    case "rename":
      return patchItem(state, action.id, (i) => ({ ...i, name: action.name, submitError: null }))
    case "setClickUrl":
      return patchItem(state, action.id, (i) => ({ ...i, clickUrl: action.clickUrl, submitError: null }))
    case "uploadStart":
      return patchItem(state, action.id, (i) => ({ ...i, phase: "uploading", progress: 0, uploadError: null }))
    case "uploadProgress":
      return patchItem(state, action.id, (i) => (i.phase === "uploading" ? { ...i, progress: action.progress } : i))
    case "uploadDone":
      return patchItem(state, action.id, (i) => {
        const width = action.width ?? i.clientWidth
        const height = action.height ?? i.clientHeight
        const c = classifyFile({ type: i.mime, size: i.size, width, height }, action.limits ?? DEFAULT_LIMITS, action.sizes)
        const reasons = [...c.reasons]
        if (action.width && action.height && (action.width !== i.clientWidth || action.height !== i.clientHeight)) {
          reasons.push({
            code: "size_mismatch",
            level: "warning",
            message: `Сервер определил размер ${action.width}×${action.height}, браузер — ${i.clientWidth ?? "?"}×${i.clientHeight ?? "?"}. Используется размер сервера.`,
          })
        }
        return { ...i, width, height, phase: "uploaded", progress: 1, assetUrl: action.assetUrl, reasons, status: statusOf(reasons), matched: c.match }
      })
    case "uploadFailed":
      return patchItem(state, action.id, (i) => ({ ...i, phase: "failed", uploadError: action.message }))
    case "retry":
      return patchItem(state, action.id, (i) => (i.phase === "failed" ? { ...i, phase: "queued", progress: 0, uploadError: null } : i))
    case "submitted": {
      const byId = new Map(action.results.map((r) => [r.client_id, r]))
      return state
        .filter((i) => byId.get(i.id)?.status !== "created")
        .map((i) => {
          const r = byId.get(i.id)
          return r ? { ...i, submitError: r.error?.message || "Ошибка создания" } : i
        })
    }
    case "reset":
      return []
  }
}

/** Файл можно отправить на создание: без ошибок и загружен на сервер. */
export function isSubmittable(i: BatchItem): boolean {
  return i.status !== "error" && i.phase === "uploaded" && !!i.assetUrl
}

export function isHttpUrl(v: string): boolean {
  return /^https?:\/\/\S+$/i.test(v.trim())
}

export interface BatchSummary {
  total: number
  ready: number
  skipped: number
  uploading: number
  uploaded: number
  /** Файлы, которые надо загрузить (без локальных ошибок). */
  toUpload: number
}

export function summarize(items: BatchItem[]): BatchSummary {
  const ready = items.filter(isSubmittable).length
  const toUpload = items.filter((i) => i.phase !== "local").length
  return {
    total: items.length,
    ready,
    skipped: items.length - ready,
    uploading: items.filter((i) => i.phase === "uploading" || i.phase === "queued").length,
    uploaded: items.filter((i) => i.phase === "uploaded").length,
    toUpload,
  }
}
