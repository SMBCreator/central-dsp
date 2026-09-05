"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import useSWR, { mutate } from "swr"
import { api, apiDeleteUpload, apiUpload, assetUrl, fetcher, type UploadResult } from "@/lib/api"
import { kktuShortLabel } from "@/lib/kktu"
import { KktuSelect } from "@/components/kktu-select"
import { AD_TYPE_LABELS, CREATIVE_ATTRS, IAB_CATEGORIES, type Campaign, type Counterparty, type OrdContract } from "@/lib/types"
import { Button, Field, inputCls, Modal } from "@/components/ui"
import { MultiSelect } from "@/components/multi-select"
import { CreativePreview } from "@/components/creative-preview"

type AdType = "banner" | "native" | "video" | "push"
type StepKey = "type" | "content" | "tracking" | "ord"

const STEPS: { key: StepKey; label: string }[] = [
  { key: "type", label: "Тип и файл" },
  { key: "content", label: "Контент" },
  { key: "tracking", label: "Трекинг" },
  { key: "ord", label: "Маркировка" },
]

const SIZES = ["300x250", "728x90", "320x50", "160x600", "300x600", "970x250"]
const IMAGE_ACCEPT = "image/png,image/jpeg,image/gif,image/webp"
const VIDEO_ACCEPT = "video/mp4,video/webm"
const IMAGE_MAX = 2 * 1024 * 1024
const VIDEO_MAX = 50 * 1024 * 1024

const TYPE_HINT: Record<AdType, string> = {
  banner: "Статичное изображение. Стандартные размеры IAB (300×250, 728×90, 320×50 и др.) определяются по файлу автоматически.",
  native: "Заголовок, описание, изображение и кнопка — подстраивается под дизайн площадки.",
  video: "In-stream / out-stream ролик MP4 или WebM. Длительность 5–120 секунд.",
  push: "Push-уведомление: короткий заголовок, текст и иконка.",
}

interface BannerAsset {
  path: string
  width: number
  height: number
  name: string
  size: number
}

interface UploadState {
  progress: number // 0..1
  fileName: string
}

interface FormState {
  campaign_id: string
  counterparty_id: string
  contract_id: string
  kktu_code: string
  kktu_label: string
  name: string
  size: string
  asset_url: string
  click_url: string
  imp_tracker_url: string
  click_tracker_url: string
  native_title: string
  native_description: string
  native_cta: string
  native_icon_url: string
  video_url: string
  video_duration: string
  /* Brand-safety для SSP: bid.cat (фильтр bcat) и bid.attr (фильтр battr) */
  iab_categories: string[]
  attrs: number[]
}

const emptyForm = (campaignId: string): FormState => ({
  campaign_id: campaignId,
  counterparty_id: "",
  contract_id: "",
  kktu_code: "",
  kktu_label: "",
  iab_categories: [],
  attrs: [],
  name: "",
  size: "300x250",
  asset_url: "",
  click_url: "",
  imp_tracker_url: "",
  click_tracker_url: "",
  native_title: "",
  native_description: "",
  native_cta: "Подробнее",
  native_icon_url: "",
  video_url: "",
  video_duration: "15",
})

function fmtBytes(n: number) {
  if (n < 1024) return `${n} Б`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} КБ`
  return `${(n / 1024 / 1024).toFixed(1).replace(".", ",")} МБ`
}

function isHttpUrl(v: string) {
  return /^https?:\/\/\S+$/i.test(v.trim())
}

function readImageSize(file: File): Promise<{ width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.crossOrigin = "anonymous"
    img.onload = () => {
      URL.revokeObjectURL(url)
      resolve({ width: img.naturalWidth, height: img.naturalHeight })
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error("Не удалось прочитать изображение"))
    }
    img.src = url
  })
}

function readVideoDuration(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file)
    const v = document.createElement("video")
    v.preload = "metadata"
    v.onloadedmetadata = () => {
      URL.revokeObjectURL(url)
      resolve(Number.isFinite(v.duration) ? Math.round(v.duration) : null)
    }
    v.onerror = () => {
      URL.revokeObjectURL(url)
      resolve(null)
    }
    v.src = url
  })
}

/** Клиентская предпроверка: до отправки на сервер отсекаем неверный тип/размер с понятным текстом. */
function precheck(file: File, kind: "image" | "video"): string | null {
  const okType = kind === "image" ? IMAGE_ACCEPT.split(",").includes(file.type) : VIDEO_ACCEPT.split(",").includes(file.type)
  if (!okType) return kind === "image" ? `«${file.name}»: допустимы PNG, JPG, GIF, WebP` : `«${file.name}»: допустимы MP4 или WebM`
  const max = kind === "image" ? IMAGE_MAX : VIDEO_MAX
  if (file.size > max) return `«${file.name}» — ${fmtBytes(file.size)}, лимит ${fmtBytes(max)}`
  return null
}

/** Результат авто-маркировки: приходит только когда создатель — админ (креатив сразу approved). */
export interface CreativeOrdResult {
  name: string
  erid: string | null
  status: "ok" | "not_required" | "awaiting_contract" | "failed" | string
  error: string | null
}

export function CreativeModal({
  open,
  onClose,
  campaigns,
  defaultCampaignId,
  onOrdResult,
}: {
  open: boolean
  onClose: () => void
  campaigns: Campaign[]
  defaultCampaignId?: string
  onOrdResult?: (r: CreativeOrdResult) => void
}) {
  const { data: counterparties } = useSWR<Counterparty[]>(open ? "/ord/counterparties" : null, fetcher)
  const { data: contracts } = useSWR<OrdContract[]>(open ? "/ord/contracts" : null, fetcher)

  const [step, setStep] = useState<StepKey>("type")
  const [type, setType] = useState<AdType>("banner")
  const [form, setForm] = useState<FormState>(() => emptyForm(defaultCampaignId || campaigns[0]?.id || ""))
  const [bannerAssets, setBannerAssets] = useState<BannerAsset[]>([])
  const [uploads, setUploads] = useState<Record<string, UploadState>>({})
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  // пути, загруженные в этой сессии модалки, но ещё не привязанные к креативу
  const orphanPaths = useRef<Set<string>>(new Set())
  const wasOpen = useRef(false)

  // Сброс при каждом открытии
  useEffect(() => {
    if (open && !wasOpen.current) {
      setStep("type")
      setType("banner")
      setForm(emptyForm(defaultCampaignId || campaigns[0]?.id || ""))
      setBannerAssets([])
      setUploads({})
      setError(null)
      setSaving(false)
      orphanPaths.current = new Set()
    }
    wasOpen.current = open
  }, [open, defaultCampaignId, campaigns])

  const selectedCp = useMemo(
    () => (counterparties || []).find((cp) => cp.id === form.counterparty_id) || null,
    [counterparties, form.counterparty_id],
  )
  // Договоры, по которым можно получить erid: зарегистрированы в ОРД и заказчик — выбранный контрагент.
  // Если такой ровно один — бэкенд подберёт его сам при approve; при нескольких выбор обязателен.
  const eligibleContracts = useMemo(
    () =>
      (contracts || []).filter(
        (c) => c.ord_status === "registered" && (!form.counterparty_id || c.client_id === form.counterparty_id),
      ),
    [contracts, form.counterparty_id],
  )
  const uploading = Object.keys(uploads).length > 0

  const patch = (p: Partial<FormState>) => setForm((f) => ({ ...f, ...p }))

  // ------------------------------------------------------------------ uploads
  const trackUpload = async (key: string, file: File) => {
    setUploads((u) => ({ ...u, [key]: { progress: 0, fileName: file.name } }))
    try {
      const res = await apiUpload<UploadResult>("/creatives/upload", file, {
        onProgress: (p) => setUploads((u) => (u[key] ? { ...u, [key]: { ...u[key], progress: p } } : u)),
      })
      const path = res.path || res.url
      orphanPaths.current.add(path)
      return { ...res, path }
    } finally {
      setUploads((u) => {
        const { [key]: _drop, ...rest } = u
        return rest
      })
    }
  }

  const uploadBannerFiles = async (files: FileList | File[] | null) => {
    if (!files || files.length === 0) return
    setError(null)
    const list = Array.from(files)
    const bad = list.map((f) => precheck(f, "image")).find(Boolean)
    if (bad) {
      setError(bad)
      return
    }
    try {
      for (const file of list) {
        const key = `banner:${file.name}:${file.size}`
        const [dims, res] = await Promise.all([readImageSize(file), trackUpload(key, file)])
        const width = res.width || dims.width
        const height = res.height || dims.height
        setBannerAssets((prev) => [...prev, { path: res.path, width, height, name: file.name.replace(/\.[^.]+$/, ""), size: res.size }])
        if (!form.name) patch({ name: file.name.replace(/\.[^.]+$/, "") })
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка загрузки файлов")
    }
  }

  const uploadSingle = async (field: "asset_url" | "native_icon_url" | "video_url", file: File | null) => {
    if (!file) return
    setError(null)
    const bad = precheck(file, field === "video_url" ? "video" : "image")
    if (bad) {
      setError(bad)
      return
    }
    try {
      const prev = form[field]
      const res = await trackUpload(field, file)
      patch({ [field]: res.path } as Partial<FormState>)
      if (field === "video_url") {
        const d = await readVideoDuration(file)
        if (d) patch({ video_duration: String(Math.min(120, Math.max(5, d))) })
      }
      if (field === "asset_url" && type === "banner" && res.width && res.height) {
        patch({ size: `${res.width}x${res.height}` })
      }
      // заменили ранее загруженный файл — старый удаляем сразу
      if (prev && orphanPaths.current.has(prev)) {
        orphanPaths.current.delete(prev)
        void apiDeleteUpload(prev)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка загрузки файла")
    }
  }

  const removeBannerAsset = (i: number) => {
    const a = bannerAssets[i]
    setBannerAssets((prev) => prev.filter((_, j) => j !== i))
    if (a && orphanPaths.current.has(a.path)) {
      orphanPaths.current.delete(a.path)
      void apiDeleteUpload(a.path)
    }
  }

  const handleClose = () => {
    if (saving) return
    // отмена — подчищаем всё, что не легло в креатив
    for (const p of orphanPaths.current) void apiDeleteUpload(p)
    orphanPaths.current = new Set()
    onClose()
  }

  // --------------------------------------------------------------- validation
  const stepErrors = useMemo(() => {
    const e: Partial<Record<StepKey, string>> = {}
    if (!form.campaign_id) e.type = "Выберите кампанию"
    else if (type === "banner" && bannerAssets.length === 0 && !form.asset_url) e.type = "Загрузите изображение или укажите URL"
    else if ((type === "native" || type === "push") && !form.asset_url && type === "push") e.type = "Для push нужна иконка/изображение"
    else if (type === "video" && !form.video_url) e.type = "Загрузите видео или укажите URL"

    if (!form.name.trim()) e.content = "Укажите название"
    else if (!isHttpUrl(form.click_url)) e.content = "Укажите корректную ссылку перехода (https://…)"
    else if ((type === "native" || type === "push") && !form.native_title.trim()) e.content = "Укажите заголовок"
    else if (type === "video" && !(Number(form.video_duration) >= 5 && Number(form.video_duration) <= 120)) e.content = "Длительность 5–120 сек"

    if (form.imp_tracker_url && !isHttpUrl(form.imp_tracker_url)) e.tracking = "Трекер показа: некорректный URL"
    else if (form.click_tracker_url && !isHttpUrl(form.click_tracker_url)) e.tracking = "Трекер клика: некорректный URL"

    if (!form.kktu_code) e.ord = "Выберите классификатор ККТУ"
    else if (form.counterparty_id && eligibleContracts.length > 1 && !form.contract_id)
      e.ord = "У контрагента несколько договоров в ОРД — выберите, по какому регистрировать erid"
    return e
  }, [form, type, bannerAssets.length, eligibleContracts.length])

  const canSubmit = Object.keys(stepErrors).length === 0 && !uploading && !saving
  const stepIdx = STEPS.findIndex((s) => s.key === step)

  const goNext = () => {
    const err = stepErrors[step]
    if (err) {
      setError(err)
      return
    }
    setError(null)
    if (stepIdx < STEPS.length - 1) setStep(STEPS[stepIdx + 1].key)
  }

  // -------------------------------------------------------------------- save
  const save = async () => {
    // Собираем первую ошибку по порядку шагов, переводим на нужный шаг
    for (const s of STEPS) {
      const err = stepErrors[s.key]
      if (err) {
        setStep(s.key)
        setError(err)
        return
      }
    }
    setSaving(true)
    setError(null)
    try {
      const base: Record<string, unknown> = {
        campaign_id: form.campaign_id,
        name: form.name.trim(),
        type,
        counterparty_id: form.counterparty_id || null,
        contract_id: form.contract_id || null,
        kktu_code: form.kktu_code || null,
        kktu_label: form.kktu_label || null,
        iab_categories: form.iab_categories,
        attrs: form.attrs,
        click_url: form.click_url.trim(),
        imp_tracker_url: form.imp_tracker_url.trim() || null,
        click_tracker_url: form.click_tracker_url.trim() || null,
      }
      const created: string[] = []
      if (type === "banner" && bannerAssets.length > 0) {
        for (const a of bannerAssets) {
          await api("/creatives", {
            method: "POST",
            body: {
              ...base,
              name: bannerAssets.length > 1 ? `${base.name} — ${a.width}×${a.height}` : base.name,
              width: a.width,
              height: a.height,
              asset_url: a.path,
            },
          })
          created.push(a.path)
          orphanPaths.current.delete(a.path)
        }
      } else {
        const body = { ...base }
        if (type === "banner") {
          const [w, h] = form.size.split("x").map(Number)
          body.width = w
          body.height = h
          body.asset_url = form.asset_url || null
        } else if (type === "native" || type === "push") {
          body.native_title = form.native_title.trim()
          body.native_description = form.native_description.trim() || null
          body.native_cta = form.native_cta.trim() || null
          body.native_icon_url = form.native_icon_url || null
          body.asset_url = form.asset_url || null
        } else {
          body.video_url = form.video_url
          body.video_duration = Number(form.video_duration) || null
          body.width = 640
          body.height = 360
        }
        const created = await api<{ name: string; erid: string | null; ord?: { status: string; error: string | null } }>(
          "/creatives",
          { method: "POST", body },
        )
        if (created.ord && onOrdResult) {
          onOrdResult({ name: created.name, erid: created.erid, status: created.ord.status, error: created.ord.error })
        }
        orphanPaths.current.clear()
      }
      await mutate("/creatives")
      await mutate((key) => typeof key === "string" && key.startsWith("/creatives?"), undefined, { revalidate: true })
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка сохранения")
    } finally {
      setSaving(false)
    }
  }

  // ----------------------------------------------------------------- preview
  const previewSize = useMemo(() => {
    if (type === "banner" && bannerAssets.length > 0) return { w: bannerAssets[0].width, h: bannerAssets[0].height }
    const [w, h] = form.size.split("x").map(Number)
    return { w: w || 300, h: h || 250 }
  }, [type, bannerAssets, form.size])
  const previewAsset = type === "banner" && bannerAssets.length > 0 ? bannerAssets[0].path : form.asset_url

  // ------------------------------------------------------------------ render
  const dropZone = (
    <label
      onDragOver={(e) => {
        e.preventDefault()
        setDragOver(true)
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragOver(false)
        void uploadBannerFiles(e.dataTransfer.files)
      }}
      className={`flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed px-4 py-8 text-center transition-colors ${
        dragOver ? "border-primary bg-primary/5" : "border-border bg-surface hover:bg-panel"
      }`}
    >
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="text-muted">
        <path d="M12 16V4m0 0l-4 4m4-4l4 4M4 20h16" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span className="text-xs font-medium text-foreground">Выбрать файлы или перетащить сюда</span>
      <span className="text-[11px] text-muted">PNG, JPG, GIF, WebP · до {fmtBytes(IMAGE_MAX)} каждый · можно несколько</span>
      <input type="file" multiple accept={IMAGE_ACCEPT} className="sr-only" onChange={(e) => void uploadBannerFiles(e.target.files)} />
    </label>
  )

  const fileBtn = (field: "asset_url" | "native_icon_url" | "video_url", accept: string) => {
    const u = uploads[field]
    return (
      <label className="relative shrink-0 cursor-pointer overflow-hidden rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-foreground hover:bg-panel">
        {u ? (
          <>
            <span className="absolute inset-y-0 left-0 bg-primary/15 transition-[width]" style={{ width: `${Math.round(u.progress * 100)}%` }} />
            <span className="relative">{Math.round(u.progress * 100)}%</span>
          </>
        ) : (
          "Файл…"
        )}
        <input type="file" accept={accept} className="sr-only" disabled={!!u} onChange={(e) => void uploadSingle(field, e.target.files?.[0] || null)} />
      </label>
    )
  }

  const uploadRows = Object.entries(uploads)
  const progressList = uploadRows.length ? (
    <ul className="space-y-1.5" aria-live="polite">
      {uploadRows.map(([k, u]) => (
        <li key={k} className="text-xs">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-foreground">{u.fileName}</span>
            <span className="shrink-0 font-mono text-muted">{Math.round(u.progress * 100)}%</span>
          </div>
          <div className="mt-1 h-1 overflow-hidden rounded bg-panel">
            <div className="h-full bg-primary transition-[width]" style={{ width: `${Math.round(u.progress * 100)}%` }} />
          </div>
        </li>
      ))}
    </ul>
  ) : null

  return (
    <Modal open={open} onClose={handleClose} title="Новый креатив" subtitle={AD_TYPE_LABELS[type]} size="xl">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
        {/* ---------------------------------------------------------- left */}
        <div className="min-w-0 space-y-4">
          <nav aria-label="Шаги" className="flex flex-wrap gap-1 rounded-lg bg-panel p-1">
            {STEPS.map((s, i) => {
              const active = s.key === step
              const invalid = !!stepErrors[s.key]
              return (
                <button
                  key={s.key}
                  type="button"
                  onClick={() => {
                    setError(null)
                    setStep(s.key)
                  }}
                  aria-current={active ? "step" : undefined}
                  className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                    active ? "bg-card text-foreground shadow-xs" : "text-muted hover:text-foreground"
                  }`}
                >
                  <span
                    className={`flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-semibold ${
                      invalid ? "bg-warning/15 text-warning" : "bg-success/15 text-success"
                    }`}
                    aria-hidden="true"
                  >
                    {invalid ? i + 1 : "✓"}
                  </span>
                  {s.label}
                </button>
              )
            })}
          </nav>

          {step === "type" ? (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {(Object.entries(AD_TYPE_LABELS) as [AdType, string][]).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => {
                      setType(value)
                      setError(null)
                    }}
                    aria-pressed={type === value}
                    className={`rounded-lg border px-3 py-2.5 text-left transition-colors ${
                      type === value ? "border-primary bg-primary/5" : "border-border bg-card hover:bg-panel"
                    }`}
                  >
                    <span className="block text-sm font-medium text-foreground">{label}</span>
                  </button>
                ))}
              </div>
              <p className="text-xs leading-relaxed text-muted">{TYPE_HINT[type]}</p>

              <Field label="Кампания">
                <select required value={form.campaign_id} onChange={(e) => patch({ campaign_id: e.target.value })} className={inputCls}>
                  <option value="" disabled>
                    Выберите…
                  </option>
                  {campaigns.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </Field>

              {type === "banner" ? (
                <div className="space-y-3">
                  {dropZone}
                  {progressList}
                  {bannerAssets.length > 0 ? (
                    <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                      {bannerAssets.map((a, i) => (
                        <li key={`${a.path}-${i}`} className="relative rounded-lg border border-border bg-surface p-1.5">
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={assetUrl(a.path) || "/placeholder.svg"} alt={`Баннер ${a.width}×${a.height}`} className="mx-auto max-h-20 rounded object-contain" />
                          <p className="mt-1 truncate text-center font-mono text-[10px] text-muted">
                            {a.width}×{a.height} · {fmtBytes(a.size)}
                          </p>
                          <button
                            type="button"
                            onClick={() => removeBannerAsset(i)}
                            className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full border border-border bg-card text-muted hover:text-danger"
                            aria-label={`Убрать баннер ${a.width}×${a.height}`}
                          >
                            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                              <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
                            </svg>
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <details className="rounded-lg border border-border bg-surface px-3 py-2">
                      <summary className="cursor-pointer text-xs text-muted">Или указать URL изображения вручную</summary>
                      <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
                        <Field label="Размер">
                          <select value={form.size} onChange={(e) => patch({ size: e.target.value })} className={inputCls}>
                            {SIZES.map((s) => (
                              <option key={s} value={s}>
                                {s}
                              </option>
                            ))}
                          </select>
                        </Field>
                        <Field label="URL изображения">
                          <input type="url" value={form.asset_url} onChange={(e) => patch({ asset_url: e.target.value })} className={inputCls} placeholder="https://cdn.site.ru/banner.png" />
                        </Field>
                      </div>
                    </details>
                  )}
                </div>
              ) : null}

              {type === "native" || type === "push" ? (
                <div className="space-y-3">
                  <Field label={type === "push" ? "Изображение уведомления" : "Изображение"} hint="Файл до 2 МБ или внешний URL">
                    <div className="flex items-center gap-2">
                      <input type="url" value={form.asset_url} onChange={(e) => patch({ asset_url: e.target.value })} className={inputCls} placeholder="https://cdn.site.ru/native.png" />
                      {fileBtn("asset_url", IMAGE_ACCEPT)}
                    </div>
                  </Field>
                  <Field label="Иконка" hint="Квадратная, желательно 1:1. Файл или URL">
                    <div className="flex items-center gap-2">
                      <input type="url" value={form.native_icon_url} onChange={(e) => patch({ native_icon_url: e.target.value })} className={inputCls} placeholder="https://cdn.site.ru/icon.png" />
                      {fileBtn("native_icon_url", "image/png,image/jpeg,image/webp")}
                    </div>
                  </Field>
                  {progressList}
                </div>
              ) : null}

              {type === "video" ? (
                <div className="space-y-3">
                  <Field label="Видео (MP4 / WebM)" hint={`Файл до ${fmtBytes(VIDEO_MAX)} или внешний URL`}>
                    <div className="flex items-center gap-2">
                      <input type="url" value={form.video_url} onChange={(e) => patch({ video_url: e.target.value })} className={inputCls} placeholder="https://cdn.site.ru/video.mp4" />
                      {fileBtn("video_url", VIDEO_ACCEPT)}
                    </div>
                  </Field>
                  {progressList}
                </div>
              ) : null}
            </div>
          ) : null}

          {step === "content" ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Название" hint="Внутреннее имя — видно только в кабинете">
                <input required value={form.name} onChange={(e) => patch({ name: e.target.value })} className={inputCls} placeholder="Баннер 300×250 осень" />
              </Field>
              <Field label="Ссылка перехода (click URL)">
                <input type="url" required value={form.click_url} onChange={(e) => patch({ click_url: e.target.value })} className={inputCls} placeholder="https://site.ru/promo" />
              </Field>

              {type === "native" || type === "push" ? (
                <>
                  <Field label={type === "push" ? "Заголовок push" : "Заголовок"}>
                    <input required maxLength={90} value={form.native_title} onChange={(e) => patch({ native_title: e.target.value })} className={inputCls} placeholder={type === "push" ? "Ваш заказ ждёт вас" : "Скидки до 50% на всё"} />
                  </Field>
                  <Field label={type === "push" ? "Текст уведомления" : "Описание"}>
                    <input maxLength={140} value={form.native_description} onChange={(e) => patch({ native_description: e.target.value })} className={inputCls} placeholder="Только до конца недели" />
                  </Field>
                  {type === "native" ? (
                    <Field label="Кнопка (CTA)">
                      <input value={form.native_cta} onChange={(e) => patch({ native_cta: e.target.value })} className={inputCls} />
                    </Field>
                  ) : null}
                </>
              ) : null}

              {type === "video" ? (
                <Field label="Длительность, сек" hint="Определяется автоматически по файлу; для внешнего URL укажите вручную">
                  <input type="number" min="5" max="120" value={form.video_duration} onChange={(e) => patch({ video_duration: e.target.value })} className={inputCls} />
                </Field>
              ) : null}

              {type === "banner" && bannerAssets.length === 0 ? (
                <Field label="Размер">
                  <select value={form.size} onChange={(e) => patch({ size: e.target.value })} className={inputCls}>
                    {SIZES.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </Field>
              ) : null}
            </div>
          ) : null}

          {step === "tracking" ? (
            <div className="space-y-4">
              <p className="text-xs leading-relaxed text-muted">
                Опционально. Сторонние пиксели аудита (adriver, weborama, mediascope) вызываются при показе и клике дополнительно к нашим трекерам. Макросы SSP не поддерживаются — только готовые URL.
              </p>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field label="Трекер показа" hint="GET-пиксель 1×1, вызывается при отрисовке">
                  <input type="url" value={form.imp_tracker_url} onChange={(e) => patch({ imp_tracker_url: e.target.value })} className={inputCls} placeholder="https://tracker.ru/imp?..." />
                </Field>
                <Field label="Трекер клика" hint="Клик-редирект трекера, финально ведёт на click URL">
                  <input type="url" value={form.click_tracker_url} onChange={(e) => patch({ click_tracker_url: e.target.value })} className={inputCls} placeholder="https://tracker.ru/click?..." />
                </Field>
              </div>
            </div>
          ) : null}

          {step === "ord" ? (
            <div className="space-y-4">
              <p className="text-xs leading-relaxed text-muted">
                По 38-ФЗ наименование, ИНН/ОГРН и адрес рекламодателя показываются в инфо-значке на креативе и уходят в ОРД.
                erid присваивается автоматически после одобрения модератором — по договору с контрагентом.
              </p>
              <Field label="Контрагент (рекламодатель)">
                <select
                  value={form.counterparty_id}
                  onChange={(e) => patch({ counterparty_id: e.target.value, contract_id: "" })}
                  className={inputCls}
                >
                  <option value="">Не выбран</option>
                  {(counterparties || []).map((cp) => (
                    <option key={cp.id} value={cp.id}>
                      {cp.name}
                      {cp.inn ? ` · ИНН ${cp.inn}` : ""}
                    </option>
                  ))}
                </select>
              </Field>
              {selectedCp ? (
                <dl className="grid grid-cols-1 gap-x-6 gap-y-1 rounded-lg bg-panel px-3 py-2 text-xs sm:grid-cols-2">
                  <div className="flex gap-1.5">
                    <dt className="text-muted">ИНН:</dt>
                    <dd className="text-foreground">{selectedCp.inn || "—"}</dd>
                  </div>
                  <div className="flex gap-1.5">
                    <dt className="text-muted">ОГРН:</dt>
                    <dd className="text-foreground">{selectedCp.ogrn || "—"}</dd>
                  </div>
                  <div className="flex gap-1.5 sm:col-span-2">
                    <dt className="text-muted">Адрес:</dt>
                    <dd className="text-foreground">{selectedCp.address || "—"}</dd>
                  </div>
                </dl>
              ) : (
                <p className="text-xs text-warning">Без контрагента креатив не получит erid и не будет показываться в РФ. Добавить: раздел «Маркировка (ОРД)».</p>
              )}
              {selectedCp ? (
                <Field
                  label={eligibleContracts.length > 1 ? "Договор для erid *" : "Договор для erid"}
                  hint={
                    eligibleContracts.length === 0
                      ? "У контрагента нет зарегистрированного в ОРД договора — erid не будет присвоен при одобрении"
                      : eligibleContracts.length === 1
                        ? "Единственный договор подставится автоматически"
                        : "Несколько договоров — укажите, по какому регистрировать"
                  }
                >
                  <select value={form.contract_id} onChange={(e) => patch({ contract_id: e.target.value })} className={inputCls}>
                    <option value="">{eligibleContracts.length === 1 ? `Автоматически: ${eligibleContracts[0].number || "без номера"}` : "Не выбран"}</option>
                    {eligibleContracts.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.number || "без номера"}
                        {c.signed_at ? ` от ${String(c.signed_at).slice(0, 10)}` : ""}
                        {c.contractor_name ? ` · ${c.contractor_name}` : ""}
                      </option>
                    ))}
                  </select>
                </Field>
              ) : null}
              <Field label="Классификатор ККТУ *">
                <KktuSelect value={form.kktu_code} onChange={(code, label) => patch({ kktu_code: code, kktu_label: label })} />
              </Field>
              <p className="text-xs text-muted">
                {form.kktu_code ? (
                  <>
                    Категория: <span className="text-foreground">{kktuShortLabel(form.kktu_code)}</span>
                  </>
                ) : (
                  "Обязательное поле: категория товара/услуги уходит в ОРД вместе с erid."
                )}
              </p>

              <div className="flex flex-col gap-3 border-t border-border pt-4">
                <div>
                  <h4 className="text-sm font-medium text-foreground">Сигналы для площадок (brand safety)</h4>
                  <p className="text-xs text-muted">
                    Уходят в ответе биддера как bid.cat / bid.attr. Площадки с запретами (bcat / battr) отсеивают
                    несовпадающие креативы ещё до аукциона — честная разметка повышает win-rate и защищает от
                    блокировок SSP.
                  </p>
                </div>
                <Field label="IAB-категории креатива" hint="Тематика рекламируемого товара. До 10 категорий">
                  <MultiSelect
                    options={Object.entries(IAB_CATEGORIES).map(([code, label]) => ({ value: code, label, hint: code }))}
                    selected={form.iab_categories}
                    onChange={(next) => patch({ iab_categories: next.slice(0, 10) })}
                  />
                </Field>
                <Field label="Атрибуты креатива (OpenRTB)" hint="Отметьте всё, что есть в креативе: авто-звук, expandable, опросы…">
                  <MultiSelect
                    options={Object.entries(CREATIVE_ATTRS).map(([code, label]) => ({ value: code, label, hint: code }))}
                    selected={form.attrs.map(String)}
                    onChange={(next) => patch({ attrs: next.map(Number).sort((a, b) => a - b) })}
                  />
                </Field>
              </div>
            </div>
          ) : null}

          {error ? (
            <p role="alert" className="rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-xs text-danger">
              {error}
            </p>
          ) : null}

          <div className="flex items-center justify-between gap-2 border-t border-border pt-4">
            <Button variant="ghost" onClick={handleClose} disabled={saving}>
              Отмена
            </Button>
            <div className="flex gap-2">
              {stepIdx > 0 ? (
                <Button variant="secondary" onClick={() => setStep(STEPS[stepIdx - 1].key)}>
                  Назад
                </Button>
              ) : null}
              {stepIdx < STEPS.length - 1 ? (
                <Button variant="secondary" onClick={goNext} disabled={uploading}>
                  Далее
                </Button>
              ) : null}
              <Button onClick={save} disabled={!canSubmit}>
                {saving ? "Сохранение…" : uploading ? "Загрузка…" : bannerAssets.length > 1 ? `Создать ${bannerAssets.length} креатива` : "Создать креатив"}
              </Button>
            </div>
          </div>
        </div>

        {/* --------------------------------------------------------- right */}
        <aside className="min-w-0 lg:sticky lg:top-0 lg:self-start">
          <div className="rounded-lg border border-border bg-surface p-3">
            <div className="mb-2 flex items-center justify-between">
              <p className="text-xs font-semibold text-foreground">Превью</p>
              <span className="font-mono text-[10px] text-muted">
                {type === "video" ? `${form.video_duration || "—"} сек` : `${previewSize.w}×${previewSize.h}`}
              </span>
            </div>
            <div className="overflow-hidden rounded-md">
              <CreativePreview
                type={type}
                assetUrl={assetUrl(previewAsset) || null}
                width={previewSize.w}
                height={previewSize.h}
                nativeTitle={form.native_title || null}
                nativeDescription={form.native_description || null}
                nativeCta={form.native_cta || null}
                nativeIconUrl={assetUrl(form.native_icon_url) || null}
                videoUrl={assetUrl(form.video_url) || null}
                advertiser={{
                  name: selectedCp?.name,
                  inn: selectedCp?.inn,
                  ogrn: selectedCp?.ogrn,
                  address: selectedCp?.address,
                  erid: null,
                }}
              />
            </div>
            {type === "banner" && bannerAssets.length > 1 ? (
              <p className="mt-2 text-[11px] text-muted">Показан первый из {bannerAssets.length} баннеров; каждый станет отдельным креативом.</p>
            ) : null}
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px]">
              <dt className="text-muted">Кампания</dt>
              <dd className="truncate text-foreground">{campaigns.find((c) => c.id === form.campaign_id)?.name || "—"}</dd>
              <dt className="text-muted">Переход</dt>
              <dd className="truncate text-foreground">{form.click_url || "—"}</dd>
              <dt className="text-muted">ККТУ</dt>
              <dd className={form.kktu_code ? "text-foreground" : "text-warning"}>{form.kktu_code || "не задан"}</dd>
              <dt className="text-muted">Модерация</dt>
              <dd className="text-foreground">после создания — «На модерации»</dd>
            </dl>
          </div>
        </aside>
      </div>
    </Modal>
  )
}
