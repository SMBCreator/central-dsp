"use client"

import { useState } from "react"
import { apiDeleteUpload, assetUrl } from "@/lib/api"
import { FALLBACK_AD_SIZES, useAdSizes } from "@/lib/ad-sizes"
import { Button, Field, inputCls, Panel } from "@/components/ui"
import { ImageCropper } from "@/components/image-cropper"
import { WizardBannerBatch } from "@/components/creative-wizard-batch"
import type { AdvertiserInfo } from "@/lib/creative-badge"

export type AdFormat = "banner" | "native" | "video" | "push"

export type CreativeDraft = {
  name: string
  type: AdFormat
  subtype: string | null
  width: number | null
  height: number | null
  asset_url: string | null
  click_url: string
  html_snippet: string | null
  native_title: string | null
  native_description: string | null
  native_cta: string | null
  native_icon_url: string | null
  video_url: string | null
  video_duration: number | null
  vast_xml: string | null
  crop: { x: number; y: number; w: number; h: number } | null
}

export const FORMAT_META: Record<AdFormat, { label: string; description: string; specs: string }> = {
  banner: {
    label: "Баннер",
    description: "Статика, GIF или HTML5. Стандартные IAB-размеры.",
    specs: `Допустимые размеры: ${FALLBACK_AD_SIZES.map((s) => s.size).join(", ")}`,
  },
  native: {
    label: "Нативный",
    description: "Заголовок, описание, картинка, иконка, CTA — рендерится в стиле площадки.",
    specs: "Изображение 1.91:1 (1200x627), иконка 1:1 (192x192), заголовок до 25 симв., описание до 90",
  },
  video: {
    label: "Видео",
    description: "In-stream (внутри плеера) или out-stream (в контенте). VAST 3.0 + квартили.",
    specs: "MP4 H.264, 16:9 (640x360+), 6-30 сек, до 100 МБ; VPAID не поддерживается",
  },
  push: {
    label: "Push",
    description: "Push-уведомление: заголовок, текст, иконка и большая картинка.",
    specs: "Иконка 192x192 (1:1), большая картинка 360x240 (3:2), заголовок до 30 симв., текст до 45",
  },
}

export const FORMAT_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(FORMAT_META).map(([k, v]) => [k, v.label]),
)

export function emptyDraft(type: AdFormat): CreativeDraft {
  return {
    name: "", type,
    subtype: type === "banner" ? "static" : type === "video" ? "instream" : null,
    width: type === "banner" ? 300 : null, height: type === "banner" ? 250 : null,
    asset_url: null, click_url: "", html_snippet: null,
    native_title: null, native_description: null, native_cta: null, native_icon_url: null,
    video_url: null, video_duration: type === "video" ? 15 : null, vast_xml: null, crop: null,
  }
}

/** Live preview of the creative exactly as the user will see it */
export function CreativePreview({ d }: { d: CreativeDraft }) {
  if (d.type === "banner") {
    if (d.subtype === "html5" && d.html_snippet) {
      return (
        <div className="overflow-hidden rounded-xl border border-border" style={{ width: Math.min(d.width || 300, 340), height: Math.min(d.height || 250, 280) }}>
          <iframe srcDoc={d.html_snippet} title="Превью HTML5-баннера" sandbox="allow-scripts" className="h-full w-full border-0" />
        </div>
      )
    }
    return d.asset_url ? (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={assetUrl(d.asset_url) || "/placeholder.svg"} alt="Превью баннера" style={{ maxWidth: 340 }} className="rounded-xl border border-border" />
    ) : (
      <p className="text-xs text-muted">Укажите изображение</p>
    )
  }
  if (d.type === "native") {
    return (
      <div className="w-72 overflow-hidden rounded-xl border border-border bg-card">
        {d.asset_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={assetUrl(d.asset_url) || "/placeholder.svg"} alt="" className="aspect-[1.91/1] w-full object-cover" />
        ) : null}
        <div className="flex gap-2.5 p-3">
          {d.native_icon_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={assetUrl(d.native_icon_url) || "/placeholder.svg"} alt="" className="h-10 w-10 shrink-0 rounded-lg object-cover" />
          ) : null}
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-foreground">{d.native_title || "Заголовок объявления"}</p>
            <p className="line-clamp-2 text-xs text-muted">{d.native_description || "Описание объявления"}</p>
            <span className="mt-1.5 inline-block rounded-full bg-primary px-3 py-1 text-[11px] font-medium text-primary-foreground">
              {d.native_cta || "Подробнее"}
            </span>
          </div>
        </div>
        <p className="border-t border-border px-3 py-1 text-[10px] text-muted">Реклама</p>
      </div>
    )
  }
  if (d.type === "push") {
    return (
      <div className="w-80 overflow-hidden rounded-xl border border-border bg-card shadow-sm">
        <div className="flex items-center gap-2 border-b border-border px-3 py-1.5">
          <span className="text-[10px] text-muted">Chrome · site.com · сейчас</span>
        </div>
        <div className="flex gap-2.5 p-3">
          {d.native_icon_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={assetUrl(d.native_icon_url) || "/placeholder.svg"} alt="" className="h-12 w-12 shrink-0 rounded-lg object-cover" />
          ) : (
            <div className="h-12 w-12 shrink-0 rounded-lg bg-panel" />
          )}
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-foreground">{d.native_title || "Заголовок push"}</p>
            <p className="line-clamp-2 text-xs text-muted">{d.native_description || "Текст уведомления"}</p>
          </div>
        </div>
        {d.asset_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={assetUrl(d.asset_url) || "/placeholder.svg"} alt="" className="aspect-[3/2] w-full object-cover" />
        ) : null}
      </div>
    )
  }
  // video
  return d.video_url ? (
    <video src={assetUrl(d.video_url)} controls className="w-80 rounded-xl border border-border" />
  ) : d.vast_xml ? (
    <p className="w-72 text-xs text-muted">VAST XML задан — превью недоступно, проверьте в RTB-тесте</p>
  ) : (
    <p className="text-xs text-muted">Укажите URL видео (mp4) или VAST XML</p>
  )
}

/** Per-format creative editing form. Emits the finished draft via onAdd. */
export function CreativeForm({ format, onAdd }: { format: AdFormat; onAdd: (d: CreativeDraft) => void }) {
  const [d, setD] = useState<CreativeDraft>(() => emptyDraft(format))
  const adSizes = useAdSizes()
  const [err, setErr] = useState<string | null>(null)
  const set = (patch: Partial<CreativeDraft>) => setD((prev) => ({ ...prev, ...patch }))

  const validate = (): string | null => {
    if (!d.name.trim()) return "Укажите название креатива"
    if (!d.click_url.trim().startsWith("http")) return "Укажите корректную ссылку перехода (https://…)"
    if (format === "banner") {
      if (d.subtype === "html5" && !d.html_snippet) return "Для HTML5-баннера вставьте HTML-код"
      if (d.subtype !== "html5" && !d.asset_url) return "Укажите изображение баннера (URL или загрузка)"
    }
    if (format === "native" && !d.native_title) return "Укажите заголовок"
    if (format === "push") {
      if (!d.native_title) return "Укажите заголовок push"
      if (!d.asset_url) return "Добавьте большую картинку (кадрирование ниже)"
    }
    if (format === "video" && !d.video_url && !d.vast_xml) return "Укажите URL видео или VAST XML"
    return null
  }

  const submit = () => {
    const v = validate()
    setErr(v)
    if (v) return
    onAdd(d)
    setD(emptyDraft(format))
  }

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
      <div className="space-y-3">
        <Field label="Название креатива">
          <input value={d.name} onChange={(e) => set({ name: e.target.value })} className={inputCls} placeholder="Креатив 1" />
        </Field>
        <Field label="Ссылка перехода (лендинг)">
          <input value={d.click_url} onChange={(e) => set({ click_url: e.target.value })} className={inputCls} placeholder="https://site.ru/offer" />
        </Field>

        {format === "banner" ? (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Подтип">
                <select value={d.subtype || "static"} onChange={(e) => set({ subtype: e.target.value, html_snippet: e.target.value === "html5" ? d.html_snippet : null })} className={inputCls}>
                  <option value="static">Статичный (JPG/PNG)</option>
                  <option value="gif">Анимированный GIF</option>
                  <option value="html5">HTML5</option>
                </select>
              </Field>
              <Field label="Размер">
                <select
                  value={`${d.width}x${d.height}`}
                  onChange={(e) => {
                    const [w, h] = e.target.value.split("x").map(Number)
                    set({ width: w, height: h })
                  }}
                  className={inputCls}
                >
                  {adSizes.map((s) => (
                    <option key={s.size} value={s.size}>{s.size} · {s.label}</option>
                  ))}
                </select>
              </Field>
            </div>
            {d.subtype === "html5" ? (
              <Field label="HTML5-код баннера" hint={d.html_snippet && !/\$\{CLICK_URL(_ENC|_ESC|_UNESC)?\}|%%CLICK_URL_(UNESC|ESC)%%|\{CLICK_URL(_ENC)?\}|\[CLICK_URL\]/.test(d.html_snippet) ? "Макрос ${CLICK_URL} не найден: клики будут учтены маячком, но лендинг не получит cdsp_clid — конверсии не атрибуцируются" : "Полный HTML сниппет; ссылка клика — ${CLICK_URL} (мы подставим трекинг-редирект), для вложения в чужой редирект — ${CLICK_URL_ENC}"}>
                <textarea value={d.html_snippet || ""} onChange={(e) => set({ html_snippet: e.target.value })} rows={5} className={`${inputCls} font-mono text-xs`} placeholder="<div>…</div>" />
              </Field>
            ) : (
              <Field label={d.subtype === "gif" ? "URL GIF-изображения" : "URL изображения"}>
                <input value={d.asset_url || ""} onChange={(e) => set({ asset_url: e.target.value })} className={inputCls} placeholder="https://…/banner.jpg" />
              </Field>
            )}
          </>
        ) : null}

        {format === "native" || format === "push" ? (
          <>
            <Field label={`Заголовок (${format === "push" ? "до 30" : "до 25"} символов)`}>
              <input value={d.native_title || ""} maxLength={format === "push" ? 30 : 25} onChange={(e) => set({ native_title: e.target.value })} className={inputCls} />
            </Field>
            <Field label={`Описание (${format === "push" ? "до 45" : "до 90"} символов)`}>
              <input value={d.native_description || ""} maxLength={format === "push" ? 45 : 90} onChange={(e) => set({ native_description: e.target.value })} className={inputCls} />
            </Field>
            {format === "native" ? (
              <Field label="Текст кнопки (CTA)">
                <input value={d.native_cta || ""} maxLength={30} onChange={(e) => set({ native_cta: e.target.value })} className={inputCls} placeholder="Подробнее" />
              </Field>
            ) : null}
            <Panel className="p-3">
              <ImageCropper
                aspect={format === "push" ? 1.5 : 1.91}
                outWidth={format === "push" ? 360 : 1200}
                outHeight={format === "push" ? 240 : 627}
                label={format === "push" ? "Большая картинка push" : "Основное изображение"}
                onCropped={(dataUrl, crop) => set({ asset_url: dataUrl, crop })}
              />
            </Panel>
            <Panel className="p-3">
              <ImageCropper
                aspect={1}
                outWidth={192}
                outHeight={192}
                label="Иконка (1:1)"
                onCropped={(dataUrl) => set({ native_icon_url: dataUrl })}
              />
            </Panel>
          </>
        ) : null}

        {format === "video" ? (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Размещение">
                <select value={d.subtype || "instream"} onChange={(e) => set({ subtype: e.target.value })} className={inputCls}>
                  <option value="instream">In-stream (в плеере)</option>
                  <option value="outstream">Out-stream (в контенте)</option>
                </select>
              </Field>
              <Field label="Длительность, сек">
                <input type="number" min={1} max={600} value={d.video_duration || 15} onChange={(e) => set({ video_duration: Number(e.target.value) })} className={inputCls} />
              </Field>
            </div>
            <Field label="URL видеофайла (MP4)">
              <input value={d.video_url || ""} onChange={(e) => set({ video_url: e.target.value })} className={inputCls} placeholder="https://…/video.mp4" />
            </Field>
            <Field label="Или свой VAST XML" hint="Если задан — используется вместо генерируемого VAST">
              <textarea value={d.vast_xml || ""} onChange={(e) => set({ vast_xml: e.target.value })} rows={4} className={`${inputCls} font-mono text-xs`} placeholder="<VAST version=&quot;3.0&quot;>…</VAST>" />
            </Field>
          </>
        ) : null}

        {err ? <p className="text-xs text-danger">{err}</p> : null}
        <Button onClick={submit}>Добавить креатив в список</Button>
      </div>

      <div>
        <p className="mb-2 text-xs font-medium text-muted">Предпросмотр</p>
        <Panel className="flex min-h-48 items-center justify-center p-4">
          <CreativePreview d={d} />
        </Panel>
        <p className="mt-2 text-[11px] leading-relaxed text-muted">{FORMAT_META[format].specs}</p>
      </div>
    </div>
  )
}

/** Multi-creative step: add as many creatives as needed, review, remove. */
export function CreativeBuilder({
  format,
  creatives,
  onChange,
  advertiser,
}: {
  format: string
  creatives: CreativeDraft[]
  onChange: (list: CreativeDraft[]) => void
  advertiser?: AdvertiserInfo
}) {
  const f = format as AdFormat

  const removeAt = (idx: number) => {
    const c = creatives[idx]
    // Файл, загруженный пакетным загрузчиком, больше не нужен (внешние URL игнорируются).
    if (c && c.type === "banner") void apiDeleteUpload(c.asset_url)
    onChange(creatives.filter((_, j) => j !== idx))
  }

  return (
    <div className="space-y-4">
      <Panel className="p-4">
        <p className="text-sm font-semibold text-foreground">Креативы: {FORMAT_META[f].label}</p>
        <p className="mt-1 text-xs leading-relaxed text-muted">{FORMAT_META[f].description} Добавьте несколько креативов — оптимизатор сам определит лучшие.</p>
      </Panel>

      <Panel className="p-4">
        {f === "banner" ? (
          <div className="space-y-4">
            <WizardBannerBatch creatives={creatives} onChange={onChange} advertiser={advertiser} />
            <details className="rounded-lg border border-border bg-surface px-3 py-2">
              <summary className="cursor-pointer text-xs text-muted">Добавить вручную (HTML5, изображение по ссылке)</summary>
              <div className="mt-3">
                <CreativeForm format={f} onAdd={(d) => onChange([...creatives, d])} />
              </div>
            </details>
          </div>
        ) : (
          <CreativeForm format={f} onAdd={(d) => onChange([...creatives, d])} />
        )}
      </Panel>

      {creatives.length > 0 ? (
        <Panel className="p-4">
          <p className="mb-3 text-xs font-medium text-muted">Добавлено креативов: {creatives.length}</p>
          <ul className="space-y-2">
            {creatives.map((c, i) => (
              <li key={i} className="flex items-center justify-between gap-3 rounded-xl border border-border px-3 py-2">
                <div className="flex min-w-0 items-center gap-3">
                  {c.asset_url && c.type !== "video" ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={assetUrl(c.asset_url) || "/placeholder.svg"} alt="" className="h-9 w-9 shrink-0 rounded-lg object-cover" />
                  ) : (
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-panel font-mono text-[10px] text-muted">
                      {c.type === "video" ? "VID" : c.subtype === "html5" ? "H5" : "AD"}
                    </div>
                  )}
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-foreground">{c.name}</p>
                    <p className="truncate text-[11px] text-muted">
                      {c.type === "banner" ? `${c.subtype} · ${c.width}x${c.height}` : c.type === "video" ? `${c.subtype} · ${c.video_duration}с` : c.native_title}
                    </p>
                  </div>
                </div>
                <Button variant="ghost" onClick={() => removeAt(i)}>
                  Убрать
                </Button>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}
    </div>
  )
}
