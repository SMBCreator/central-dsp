"use client"

import { memo, useCallback, useEffect, useReducer, useRef, useState, type MutableRefObject } from "react"
import { apiDeleteUpload, apiUpload, assetUrl, type UploadResult } from "@/lib/api"
import { formatSize, type AdSize } from "@/lib/ad-sizes-catalog"
import { AdInfoBadge, CreativePreview } from "@/components/creative-preview"
import type { AdvertiserInfo } from "@/lib/creative-badge"
import {
  BATCH_MAX_FILES,
  IMAGE_ACCEPT,
  IMAGE_MAX_BYTES,
  IMAGE_TYPES,
  UPLOAD_CONCURRENCY,
  batchReducer,
  collectFiles,
  fmtBytes,
  makeItem,
  summarize,
  type BatchItem,
  type BatchResultItem,
  type EntryLike,
} from "@/lib/creative-batch"

async function readImageDims(file: File): Promise<{ width: number; height: number } | null> {
  try {
    if (typeof createImageBitmap === "function") {
      const bmp = await createImageBitmap(file)
      const dims = { width: bmp.width, height: bmp.height }
      bmp.close?.()
      return dims
    }
  } catch {
    // запасной путь — <img>
  }
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      URL.revokeObjectURL(url)
      resolve({ width: img.naturalWidth, height: img.naturalHeight })
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      resolve(null)
    }
    img.src = url
  })
}

// Сколько файлов максимум просматриваем в перетащенной папке (чтобы честно
// посчитать «не добавлено» сверх BATCH_MAX_FILES и не зависнуть на огромной папке).
const FOLDER_SCAN_MAX = 500

let seq = 0
const nextId = () => `f${Date.now().toString(36)}${(seq++).toString(36)}`

/**
 * Состояние пакета и очередь загрузки (параллелизм UPLOAD_CONCURRENCY).
 * Загруженные, но не привязанные к креативу файлы попадают в orphanPaths модалки —
 * при закрытии она их удаляет (существующий механизм).
 */
export function useCreativeBatch(sizes: AdSize[], orphanPaths: MutableRefObject<Set<string>>) {
  const [items, dispatch] = useReducer(batchReducer, [])
  const [notices, setNotices] = useState<string[]>([])
  const itemsRef = useRef(items)
  const sizesRef = useRef(sizes)
  const files = useRef(new Map<string, File>())
  const controllers = useRef(new Map<string, AbortController>())
  const queue = useRef<string[]>([])
  const active = useRef(0)
  const count = useRef(0)

  useEffect(() => {
    itemsRef.current = items
  }, [items])
  useEffect(() => {
    sizesRef.current = sizes
  }, [sizes])

  const pump = useCallback(() => {
    while (active.current < UPLOAD_CONCURRENCY && queue.current.length > 0) {
      const id = queue.current.shift() as string
      const file = files.current.get(id)
      if (!file) continue
      active.current++
      const ctrl = new AbortController()
      controllers.current.set(id, ctrl)
      dispatch({ type: "uploadStart", id })
      apiUpload<UploadResult>("/creatives/upload", file, {
        signal: ctrl.signal,
        silent: true,
        onProgress: (progress) => dispatch({ type: "uploadProgress", id, progress }),
      })
        .then((res) => {
          // Абсолютный URL: CreativeUrl на сервере принимает только http(s) с хостом.
          const url = res.url || res.path
          if (ctrl.signal.aborted || !files.current.has(id)) {
            void apiDeleteUpload(url)
            return
          }
          orphanPaths.current.add(url)
          dispatch({ type: "uploadDone", id, assetUrl: url, width: res.width ?? null, height: res.height ?? null, sizes: sizesRef.current })
        })
        .catch((err: unknown) => {
          if (!ctrl.signal.aborted) dispatch({ type: "uploadFailed", id, message: err instanceof Error ? err.message : "Ошибка загрузки" })
        })
        .finally(() => {
          controllers.current.delete(id)
          active.current--
          pump()
        })
    }
  }, [orphanPaths])

  const addFiles = useCallback(
    async (list: File[], extra: { skipped?: number; truncated?: boolean } = {}) => {
      const room = Math.max(0, BATCH_MAX_FILES - count.current)
      const accepted = list.slice(0, room)
      const msgs: string[] = []
      if (list.length > room) msgs.push(`В пакете не больше ${BATCH_MAX_FILES} файлов — не добавлено: ${list.length - room}.`)
      if (extra.truncated) msgs.push("Папка слишком большая или глубокая — добавлена только часть файлов.")
      if (extra.skipped) msgs.push(`Пропущено файлов, не являющихся изображениями: ${extra.skipped}.`)
      setNotices(msgs)
      if (accepted.length === 0) return
      count.current += accepted.length
      const created = await Promise.all(
        accepted.map(async (file) => {
          const isImage = IMAGE_TYPES.includes(file.type)
          const dims = isImage ? await readImageDims(file) : null
          const item = makeItem(
            {
              id: nextId(),
              fileName: file.name,
              size: file.size,
              mime: file.type,
              previewUrl: isImage && dims ? URL.createObjectURL(file) : null,
              width: dims?.width ?? null,
              height: dims?.height ?? null,
            },
            sizesRef.current,
          )
          return { item, file }
        }),
      )
      for (const { item, file } of created) {
        if (item.phase === "queued") {
          files.current.set(item.id, file)
          queue.current.push(item.id)
        }
      }
      dispatch({ type: "add", items: created.map((c) => c.item) })
      pump()
    },
    [pump],
  )

  const forget = useCallback((item: BatchItem | undefined, deleteUpload: boolean) => {
    if (!item) return
    controllers.current.get(item.id)?.abort()
    queue.current = queue.current.filter((q) => q !== item.id)
    files.current.delete(item.id)
    if (item.previewUrl) URL.revokeObjectURL(item.previewUrl)
    if (deleteUpload && item.assetUrl && orphanPaths.current.has(item.assetUrl)) {
      orphanPaths.current.delete(item.assetUrl)
      void apiDeleteUpload(item.assetUrl)
    }
    count.current = Math.max(0, count.current - 1)
  }, [orphanPaths])

  const remove = useCallback(
    (id: string) => {
      forget(itemsRef.current.find((i) => i.id === id), true)
      dispatch({ type: "remove", id })
    },
    [forget],
  )
  const rename = useCallback((id: string, name: string) => dispatch({ type: "rename", id, name }), [])
  const setClickUrl = useCallback((id: string, clickUrl: string) => dispatch({ type: "setClickUrl", id, clickUrl }), [])
  const retry = useCallback(
    (id: string) => {
      if (!files.current.has(id)) return
      queue.current.push(id)
      dispatch({ type: "retry", id })
      pump()
    },
    [pump],
  )

  /** Успешно созданные убираются из списка (файл уже принадлежит креативу), ошибки остаются. */
  const applyResults = useCallback(
    (results: BatchResultItem[]) => {
      for (const r of results) {
        if (r.status !== "created") continue
        const item = itemsRef.current.find((i) => i.id === r.client_id)
        if (item?.assetUrl) orphanPaths.current.delete(item.assetUrl)
        forget(item, false)
      }
      dispatch({ type: "submitted", results })
    },
    [forget, orphanPaths],
  )

  /** Закрытие модалки: отменить загрузки, освободить превью. «Висящие» файлы удалит модалка. */
  const cancelAll = useCallback(() => {
    for (const c of controllers.current.values()) c.abort()
    controllers.current.clear()
    queue.current = []
    files.current.clear()
    for (const i of itemsRef.current) if (i.previewUrl) URL.revokeObjectURL(i.previewUrl)
    count.current = 0
    setNotices([])
    dispatch({ type: "reset" })
  }, [])

  useEffect(() => () => cancelAll(), [cancelAll])

  return { items, notices, addFiles, remove, rename, setClickUrl, retry, applyResults, cancelAll }
}

export type CreativeBatch = ReturnType<typeof useCreativeBatch>

// ------------------------------------------------------------------- UI

const STATUS_META: Record<BatchItem["status"], { label: string; cls: string; border: string }> = {
  ready: { label: "Готов", cls: "bg-success/10 text-success", border: "border-border" },
  warning: { label: "Предупреждение", cls: "bg-warning/10 text-warning", border: "border-warning/40" },
  error: { label: "Ошибка", cls: "bg-danger/10 text-danger", border: "border-danger/40" },
}

function phaseLabel(i: BatchItem): string | null {
  if (i.phase === "queued") return "В очереди"
  if (i.phase === "uploading") return `Загрузка ${Math.round(i.progress * 100)}%`
  if (i.phase === "failed") return "Не загружен"
  return null
}

const smallInputCls =
  "w-full rounded-md border border-input bg-card px-2.5 py-1 text-sm text-foreground placeholder:text-muted focus:border-ring focus:ring-2 focus:ring-ring/40 focus:outline-none"

const NO_ADVERTISER: AdvertiserInfo = {}

interface CardProps {
  item: BatchItem
  /** Стабильная ссылка (useMemo у владельца): карточка обёрнута в memo, пакет до 30 файлов. */
  advertiser?: AdvertiserInfo
  defaultClickUrl: string
  onRename: (id: string, name: string) => void
  onClickUrl: (id: string, url: string) => void
  onRemove: (id: string) => void
  onRetry: (id: string) => void
}

export const BatchItemCard = memo(function BatchItemCard({ item, advertiser = NO_ADVERTISER, defaultClickUrl, onRename, onClickUrl, onRemove, onRetry }: CardProps) {
  const [expanded, setExpanded] = useState(false)
  const meta = STATUS_META[item.submitError ? "error" : item.status]
  const phase = phaseLabel(item)
  const src = item.previewUrl || assetUrl(item.assetUrl) || null
  const dims = item.width && item.height ? `${item.width}×${item.height}` : "размер не определён"
  const nameId = `batch-name-${item.id}`
  const urlId = `batch-url-${item.id}`

  return (
    <li className={`rounded-lg border bg-card p-3 ${meta.border}`} data-batch-item={item.id}>
      <div className="flex flex-col gap-3 sm:flex-row">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          disabled={!src}
          aria-expanded={expanded}
          aria-label={`${expanded ? "Свернуть" : "Увеличить"} превью «${item.fileName}»`}
          className="flex h-32 w-full shrink-0 items-center justify-center overflow-hidden rounded-md border border-border bg-panel p-1 disabled:cursor-default sm:w-40"
        >
          {src ? (
            // eslint-disable-next-line @next/next/no-img-element
            <span className="relative inline-block max-h-full max-w-full overflow-hidden rounded-sm">
              <img src={src || "/placeholder.svg"} alt="" className="block max-h-[7.5rem] max-w-full object-contain" />
              <AdInfoBadge advertiser={advertiser} compact interactive={false} />
            </span>
          ) : (
            <span className="text-[11px] text-muted">Нет превью</span>
          )}
        </button>

        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 space-y-1">
              <p className="truncate text-sm font-medium text-foreground" title={item.fileName}>
                {item.fileName}
              </p>
              <p className="font-mono text-[11px] text-muted">
                {dims} · {fmtBytes(item.size)}
              </p>
            </div>
            <button
              type="button"
              onClick={() => onRemove(item.id)}
              className="shrink-0 rounded-md px-2 py-1 text-xs text-muted hover:bg-panel hover:text-danger"
              aria-label={`Убрать файл «${item.fileName}»`}
            >
              Убрать
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${meta.cls}`}>{meta.label}</span>
            {item.matched ? (
              <span className="inline-flex items-center rounded-md bg-panel px-2 py-0.5 font-mono text-[11px] text-foreground">
                {formatSize(item.matched)} · {item.matched.label}
              </span>
            ) : null}
            {phase ? <span className="text-[11px] text-muted">{phase}</span> : null}
          </div>

          {item.phase === "uploading" ? (
            <div className="h-1 overflow-hidden rounded bg-panel" aria-hidden="true">
              <div className="h-full bg-primary transition-[width]" style={{ width: `${Math.round(item.progress * 100)}%` }} />
            </div>
          ) : null}

          {item.reasons.length > 0 || item.uploadError || item.submitError ? (
            <ul className="space-y-0.5 text-xs leading-relaxed">
              {item.reasons.map((r, idx) => (
                <li key={`${r.code}-${idx}`} className={r.level === "error" ? "text-danger" : "text-warning"}>
                  {r.level === "error" ? "Ошибка: " : "Предупреждение: "}
                  {r.message}
                </li>
              ))}
              {item.uploadError ? <li className="text-danger">Ошибка загрузки: {item.uploadError}</li> : null}
              {item.submitError ? <li className="text-danger">Не создан: {item.submitError}</li> : null}
            </ul>
          ) : null}

          {item.phase === "failed" ? (
            <button
              type="button"
              onClick={() => onRetry(item.id)}
              className="rounded-md border border-border bg-card px-2.5 py-1 text-xs font-medium text-foreground hover:bg-panel"
            >
              Повторить загрузку
            </button>
          ) : null}

          {item.status !== "error" ? (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <div>
                <label htmlFor={nameId} className="mb-1 block text-[11px] font-medium text-muted">
                  Название
                </label>
                <input id={nameId} value={item.name} maxLength={200} onChange={(e) => onRename(item.id, e.target.value)} className={smallInputCls} />
              </div>
              <div>
                <label htmlFor={urlId} className="mb-1 block text-[11px] font-medium text-muted">
                  Ссылка перехода
                </label>
                <input
                  id={urlId}
                  type="url"
                  value={item.clickUrl}
                  onChange={(e) => onClickUrl(item.id, e.target.value)}
                  className={smallInputCls}
                  placeholder={defaultClickUrl ? `Общая: ${defaultClickUrl}` : "Общая ссылка (шаг «Контент»)"}
                />
              </div>
            </div>
          ) : null}
        </div>
      </div>

      {expanded && src ? (
        <div className="mt-3 overflow-auto rounded-md border border-border bg-panel p-2">
          {item.width && item.height ? (
            <CreativePreview fit="contain" type="banner" assetUrl={src} width={item.width} height={item.height} advertiser={advertiser} />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={src || "/placeholder.svg"} alt={`Баннер ${dims}`} className="mx-auto max-w-full" />
          )}
        </div>
      ) : null}
    </li>
  )
})

export function BatchItemList({ batch, defaultClickUrl, advertiser }: { batch: CreativeBatch; defaultClickUrl: string; advertiser?: AdvertiserInfo }) {
  if (batch.items.length === 0) return null
  return (
    <ul className="space-y-2" aria-label="Файлы пакета">
      {batch.items.map((item) => (
        <BatchItemCard
          key={item.id}
          item={item}
          advertiser={advertiser}
          defaultClickUrl={defaultClickUrl}
          onRename={batch.rename}
          onClickUrl={batch.setClickUrl}
          onRemove={batch.remove}
          onRetry={batch.retry}
        />
      ))}
    </ul>
  )
}

function UploadIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="text-muted">
      <path d="M12 16V4m0 0l-4 4m4-4l4 4M4 20h16" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function CreativeBatchPanel({ batch, defaultClickUrl, advertiser }: { batch: CreativeBatch; defaultClickUrl: string; advertiser?: AdvertiserInfo }) {
  const [dragOver, setDragOver] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)
  const s = summarize(batch.items)
  const failed = batch.items.filter((i) => i.phase === "failed")

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    // webkitGetAsEntry доступен только синхронно, в обработчике drop
    const entries = Array.from(e.dataTransfer.items || [])
      .map((it) => (typeof it.webkitGetAsEntry === "function" ? (it.webkitGetAsEntry() as unknown as EntryLike | null) : null))
      .filter((x): x is EntryLike => !!x)
    if (entries.length > 0) {
      void collectFiles(entries, { maxFiles: FOLDER_SCAN_MAX }).then((r) =>
        batch.addFiles(r.files, { skipped: r.skipped, truncated: r.truncated }),
      )
    } else {
      void batch.addFiles(Array.from(e.dataTransfer.files || []))
    }
  }

  const onFolderPicked = (list: FileList | null) => {
    const all = Array.from(list || [])
    const images = all.filter((f) => IMAGE_TYPES.includes(f.type) && !f.name.startsWith("."))
    void batch.addFiles(images, { skipped: all.length - images.length })
  }

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        className={`flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-4 py-6 text-center transition-colors ${
          dragOver ? "border-primary bg-primary/5" : "border-border bg-surface"
        }`}
      >
        <UploadIcon />
        <p className="text-xs font-medium text-foreground">Перетащите файлы или папку с баннерами</p>
        <div className="flex flex-wrap justify-center gap-2">
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/85"
          >
            Выбрать файлы
          </button>
          <button
            type="button"
            onClick={() => folderInput.current?.click()}
            className="rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground hover:bg-panel"
          >
            Выбрать папку
          </button>
        </div>
        <p className="text-[11px] text-muted">
          PNG, JPG, GIF, WebP · до {fmtBytes(IMAGE_MAX_BYTES)} каждый · до {BATCH_MAX_FILES} файлов · размер определяется автоматически
        </p>
        <input
          ref={fileInput}
          type="file"
          multiple
          accept={IMAGE_ACCEPT}
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(e) => {
            void batch.addFiles(Array.from(e.target.files || []))
            e.target.value = ""
          }}
        />
        <input
          ref={folderInput}
          type="file"
          multiple
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          {...{ webkitdirectory: "" }}
          onChange={(e) => {
            onFolderPicked(e.target.files)
            e.target.value = ""
          }}
        />
      </div>

      {batch.notices.length > 0 ? (
        <ul role="status" className="space-y-1 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning">
          {batch.notices.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}

      {s.toUpload > 0 ? (
        <div aria-live="polite">
          <div className="flex items-center justify-between gap-2 text-xs">
            <span className="text-foreground">
              Загружено {s.uploaded} из {s.toUpload}
            </span>
            {failed.length > 1 ? (
              <button
                type="button"
                onClick={() => failed.forEach((i) => batch.retry(i.id))}
                className="rounded-md px-2 py-0.5 text-xs font-medium text-primary hover:bg-panel"
              >
                Повторить все неудачные ({failed.length})
              </button>
            ) : null}
          </div>
          <div className="mt-1 h-1 overflow-hidden rounded bg-panel" aria-hidden="true">
            <div className="h-full bg-primary transition-[width]" style={{ width: `${Math.round((s.uploaded / s.toUpload) * 100)}%` }} />
          </div>
        </div>
      ) : null}

      <BatchItemList batch={batch} defaultClickUrl={defaultClickUrl} advertiser={advertiser} />

      {s.total > 0 ? (
        <p className="text-xs text-muted" aria-live="polite">
          {s.uploading > 0
            ? `Идёт загрузка: ${s.uploading}`
            : `Будет создано: ${s.ready}${s.skipped ? `, пропущено: ${s.skipped}` : ""}`}
        </p>
      ) : null}
    </div>
  )
}

// ------------------------------------------------------------ results

export interface BatchResultRow {
  key: string
  fileName: string
  name: string
  ok: boolean
  message: string | null
  ord: { status: string; error: string | null; erid: string | null } | null
  duplicate: boolean
}

function ordText(r: BatchResultRow): { text: string; cls: string } {
  if (!r.ok) return { text: "—", cls: "text-muted" }
  if (!r.ord) return { text: "на модерации", cls: "text-muted" }
  if (r.ord.status === "ok") return { text: `erid ${r.ord.erid ?? ""}`.trim(), cls: "text-success" }
  if (r.ord.status === "not_required") return { text: "не требуется", cls: "text-muted" }
  if (r.ord.status === "awaiting_contract") return { text: "ждёт договор", cls: "text-warning" }
  return { text: `ошибка ОРД${r.ord.error ? `: ${r.ord.error}` : ""}`, cls: "text-danger" }
}

export function BatchResults({ rows, showOrd }: { rows: BatchResultRow[]; showOrd: boolean }) {
  if (rows.length === 0) return null
  const okCount = rows.filter((r) => r.ok).length
  return (
    <section aria-live="polite" className="rounded-lg border border-border bg-surface p-3">
      <h4 className="text-xs font-semibold text-foreground">
        Итог: создано {okCount} из {rows.length}
      </h4>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-muted">
            <tr>
              <th scope="col" className="py-1 pr-3 font-medium">Файл</th>
              <th scope="col" className="py-1 pr-3 font-medium">Результат</th>
              {showOrd ? <th scope="col" className="py-1 font-medium">Маркировка</th> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const o = ordText(r)
              return (
                <tr key={r.key} className="border-t border-border align-top">
                  <td className="max-w-[12rem] truncate py-1.5 pr-3 text-foreground" title={r.fileName}>
                    {r.name}
                  </td>
                  <td className={`py-1.5 pr-3 ${r.ok ? "text-success" : "text-danger"}`}>
                    {r.ok ? (r.duplicate ? "уже создан ранее" : "создан") : `ошибка: ${r.message}`}
                  </td>
                  {showOrd ? <td className={`py-1.5 ${o.cls}`}>{o.text}</td> : null}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}
