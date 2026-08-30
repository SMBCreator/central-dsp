"use client"

import { useState } from "react"
import useSWR, { mutate } from "swr"
import { api, apiUpload, assetUrl, fetcher } from "@/lib/api"
import { kktuShortLabel } from "@/lib/kktu"
import { KktuSelect } from "@/components/kktu-select"
import { AD_TYPE_LABELS, type Campaign, type Counterparty, type Creative } from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Field, inputCls, Modal, Spinner, StatusBadge, Table } from "@/components/ui"
import { CreativePreview } from "@/components/creative-preview"

const SIZES = ["300x250", "728x90", "320x50", "160x600", "300x600", "970x250"]

export default function CreativesPage() {
  const { data: creatives } = useSWR<Creative[]>("/creatives", fetcher)
  const { data: campaigns } = useSWR<Campaign[]>("/campaigns", fetcher)
  const { data: counterparties } = useSWR<Counterparty[]>("/ord/counterparties", fetcher)
  
  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [uploading, setUploading] = useState<string | null>(null)
  const [type, setType] = useState<"banner" | "native" | "video">("banner")
  const [form, setForm] = useState({
    campaign_id: "",
    counterparty_id: "",
    kktu_code: "",
    kktu_label: "",
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

  const selectedCp = (counterparties || []).find((cp) => cp.id === form.counterparty_id) || null

  // D: мультизагрузка баннеров — каждый файл станет отдельным креативом, размер определяется автоматически
  const [bannerAssets, setBannerAssets] = useState<{ path: string; width: number; height: number; name: string }[]>([])

  const readImageSize = (file: File): Promise<{ width: number; height: number }> =>
    new Promise((resolve, reject) => {
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

  const uploadBannerFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setError(null)
    setUploading("asset_url")
    try {
      for (const file of Array.from(files)) {
        const [{ width, height }, res] = await Promise.all([
          readImageSize(file),
          apiUpload<{ url: string; path: string }>("/creatives/upload", file),
        ])
        setBannerAssets((prev) => [
          ...prev,
          { path: res.path || res.url, width, height, name: file.name.replace(/\.[^.]+$/, "") },
        ])
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка загрузки файлов")
    } finally {
      setUploading(null)
    }
  }

  const uploadFile = async (field: "asset_url" | "native_icon_url" | "video_url", file: File | null) => {
    if (!file) return
    setError(null)
    setUploading(field)
    try {
      const res = await apiUpload<{ url: string; path: string }>("/creatives/upload", file)
      // Храним канонический относительный путь (/uploads/<file>); в браузер он
      // отдаётся через assetUrl() как /api/uploads/<file> и работает и в dev, и в проде.
      setForm((f) => ({ ...f, [field]: res.path || res.url }))
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка загрузки файла")
    } finally {
      setUploading(null)
    }
  }

  const openCreate = () => {
    setError(null)
    setType("banner")
    setBannerAssets([])
    setForm({
      campaign_id: campaigns?.[0]?.id || "",
      counterparty_id: "",
      kktu_code: "",
      kktu_label: "",
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
    setOpen(true)
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    // Классификатор ККТУ обязателен для маркировки (38-ФЗ)
    if (!form.kktu_code) {
      setError("Выберите классификатор ККТУ на шаге «Маркировка» — это обязательное поле для регистрации в ОРД.")
      return
    }
    // G7: для баннера нужен хотя бы один файл или URL
    if (type === "banner" && bannerAssets.length === 0 && !form.asset_url) {
      setError("Загрузите хотя бы одно изображение баннера или укажите URL.")
      return
    }
    setSaving(true)
    setError(null)
    try {
      const [w, h] = form.size.split("x").map(Number)
      const body: Record<string, unknown> = {
        campaign_id: form.campaign_id,
        name: form.name,
        type,
        counterparty_id: form.counterparty_id || null,
        kktu_code: form.kktu_code || null,
        kktu_label: form.kktu_label || null,
        click_url: form.click_url || null,
        imp_tracker_url: form.imp_tracker_url || null,
        click_tracker_url: form.click_tracker_url || null,
      }
      if (type === "banner" && bannerAssets.length > 0) {
        // D: пакетное создание — каждый загруженный файл становится отдельным креативом
        for (let i = 0; i < bannerAssets.length; i++) {
          const a = bannerAssets[i]
          await api("/creatives", {
            method: "POST",
            body: {
              ...body,
              name: bannerAssets.length > 1 ? `${form.name} — ${a.width}×${a.height}` : form.name,
              width: a.width,
              height: a.height,
              asset_url: a.path,
            },
          })
        }
        await mutate("/creatives")
        setBannerAssets([])
        setOpen(false)
        setSaving(false)
        return
      }
      if (type === "banner") {
        body.width = w
        body.height = h
        body.asset_url = form.asset_url || null
      } else if (type === "native") {
        body.native_title = form.native_title
        body.native_description = form.native_description || null
        body.native_cta = form.native_cta || null
        body.native_icon_url = form.native_icon_url || null
        body.asset_url = form.asset_url || null
      } else {
        body.video_url = form.video_url
        body.video_duration = Number(form.video_duration) || null
        body.width = 640
        body.height = 360
      }
      await api("/creatives", { method: "POST", body })
      await mutate("/creatives")
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка сохранения")
    } finally {
      setSaving(false)
    }
  }

  const moderate = async (c: Creative, status: string) => {
    await api(`/creatives/${c.id}`, { method: "PATCH", body: { status } })
    await mutate("/creatives")
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Креативы</h1>
          <p className="mt-0.5 text-sm text-muted">Баннеры, нативные и видеообъявления</p>
        </div>
        <Button onClick={openCreate} disabled={!campaigns || campaigns.length === 0}>
          Загрузить креатив
        </Button>
      </div>

      <Card>
        <CardHeader title="Все креативы" subtitle={creatives ? `Всего: ${creatives.length}` : undefined} />
        {creatives ? (
          creatives.length > 0 ? (
            <Table headers={["ID", "Название", "Тип", "Классификатор", "Размер", "Статус", "Кампания", ""]}>
              {creatives.map((c) => {
                const camp = campaigns?.find((x) => x.id === c.campaign_id)
                return (
                  <tr key={c.id} className="hover:bg-surface">
                    <td className="px-4 py-3 font-mono text-xs text-muted">{c.display_id}</td>
                    <td className="px-4 py-3 font-medium text-foreground">{c.name}</td>
                    <td className="px-4 py-3 text-muted">{AD_TYPE_LABELS[c.type] || c.type}</td>
                    <td className="px-4 py-3 text-muted">
                      {c.kktu_code ? (
                        <span title={kktuShortLabel(c.kktu_code)} className="inline-flex items-center gap-1.5">
                          <span className="font-mono text-xs text-foreground">{c.kktu_code}</span>
                          <span className="max-w-40 truncate text-xs">{c.kktu_label || ""}</span>
                        </span>
                      ) : (
                        <span className="text-xs text-warning">не задан</span>
                      )}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-muted">
                      {c.width && c.height ? `${c.width}×${c.height}` : c.video_duration ? `${c.video_duration} сек` : "—"}
                    </td>
                    <td className="px-4 py-3" title={c.moderation_comment || undefined}>
                      <StatusBadge status={c.status} />
                      {c.status === "rejected" && c.moderation_comment ? (
                        <p className="mt-1 max-w-48 text-[10px] leading-snug text-danger">{c.moderation_comment}</p>
                      ) : null}
                    </td>
                    <td className="px-4 py-3 text-muted">{camp?.name || "—"}</td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-2">
                        {/* H10: открыть креатив в реальном размере в новой вкладке */}
                        {c.asset_url || c.video_url ? (
                          <a
                            href={assetUrl(c.asset_url || c.video_url || "") || "#"}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center rounded-md px-2 py-1 text-xs font-medium text-muted transition-colors hover:text-foreground"
                            title="Открыть в реальном размере в новой вкладке"
                          >
                            Открыть
                            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="ml-1">
                              <path d="M7 17L17 7M17 7H8M17 7v9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                            </svg>
                          </a>
                        ) : null}
                        {c.status === "pending" ? (
                          <>
                            <Button variant="ghost" onClick={() => moderate(c, "approved")}>
                              Одобрить
                            </Button>
                            <Button variant="ghost" onClick={() => moderate(c, "rejected")}>
                              Отклонить
                            </Button>
                          </>
                        ) : c.status === "rejected" ? (
                          <Button variant="ghost" onClick={() => moderate(c, "pending")}>
                            На модерацию
                          </Button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </Table>
          ) : (
            <EmptyState
              title="Нет креативов"
              description="Загрузите баннер, нативное или видеообъявление для показа в кампаниях."
            />
          )
        ) : (
          <Spinner />
        )}
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Новый креатив" wide>
        <form onSubmit={save} className="space-y-4">
          <div className="flex gap-1 rounded-md border border-border bg-surface p-0.5">
            {(Object.entries(AD_TYPE_LABELS) as ["banner" | "native" | "video", string][]).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setType(value)}
                className={`flex-1 rounded px-3 py-1.5 text-xs font-medium transition-colors ${
                  type === value ? "bg-primary text-primary-foreground" : "text-muted hover:text-foreground"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Кампания">
              <select
                required
                value={form.campaign_id}
                onChange={(e) => setForm({ ...form, campaign_id: e.target.value })}
                className={inputCls}
              >
                <option value="" disabled>
                  Выберите…
                </option>
                {(campaigns || []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Название">
              <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={inputCls} placeholder="Баннер 300×250 осень" />
            </Field>
            <Field label="Ссылка перехода (click URL)">
              <input type="url" required value={form.click_url} onChange={(e) => setForm({ ...form, click_url: e.target.value })} className={inputCls} placeholder="https://site.ru/promo" />
            </Field>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Сторонний трекер показа" hint="Опционально: пиксель аудита (adriver, weborama...)">
              <input type="url" value={form.imp_tracker_url} onChange={(e) => setForm({ ...form, imp_tracker_url: e.target.value })} className={inputCls} placeholder="https://tracker.ru/imp?..." />
            </Field>
            <Field label="Сторонний трекер клика" hint="Опционально: клик-редирект трекера">
              <input type="url" value={form.click_tracker_url} onChange={(e) => setForm({ ...form, click_tracker_url: e.target.value })} className={inputCls} placeholder="https://tracker.ru/click?..." />
            </Field>
          </div>

            {type === "banner" ? (
              <>
                {/* D: мультизагрузка — несколько файлов, размер определяется автоматически */}
                <Field label="Изображения" hint="Можно выбрать несколько файлов — размеры определятся автоматически, каждый файл станет отдельным креативом">
                  <label className="flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-md border border-dashed border-border bg-surface px-4 py-6 text-center transition-colors hover:bg-panel">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="text-muted">
                      <path d="M12 16V4m0 0l-4 4m4-4l4 4M4 20h16" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    <span className="text-xs font-medium text-foreground">
                      {uploading === "asset_url" ? "Загрузка…" : "Выбрать файлы (PNG, JPG, GIF, WebP)"}
                    </span>
                    <span className="text-[11px] text-muted">или перетащите сюда</span>
                    <input
                      type="file"
                      multiple
                      accept="image/png,image/jpeg,image/gif,image/webp"
                      className="sr-only"
                      onChange={(e) => uploadBannerFiles(e.target.files)}
                    />
                  </label>
                </Field>
                {bannerAssets.length > 0 ? (
                  <div className="flex flex-wrap gap-2">
                    {bannerAssets.map((a, i) => (
                      <div key={`${a.path}-${i}`} className="relative rounded-md border border-border bg-surface p-1.5">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={assetUrl(a.path) || "/placeholder.svg"} alt={`Баннер ${a.width}×${a.height}`} className="max-h-20 rounded object-contain" />
                        <p className="mt-1 text-center font-mono text-[10px] text-muted">{a.width}×{a.height}</p>
                        <button
                          type="button"
                          onClick={() => setBannerAssets((prev) => prev.filter((_, j) => j !== i))}
                          className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full border border-border bg-panel text-muted hover:text-danger"
                          aria-label={`Убрать баннер ${a.width}×${a.height}`}
                        >
                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                            <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
                          </svg>
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <details className="rounded-md border border-border bg-surface px-3 py-2">
                    <summary className="cursor-pointer text-xs text-muted">Или укажите URL изображения вручную</summary>
                    <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <Field label="Размер">
                        <select value={form.size} onChange={(e) => setForm({ ...form, size: e.target.value })} className={inputCls}>
                          {SIZES.map((s) => (
                            <option key={s} value={s}>
                              {s}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <Field label="URL изображения">
                        <input type="url" value={form.asset_url} onChange={(e) => setForm({ ...form, asset_url: e.target.value })} className={inputCls} placeholder="https://cdn.site.ru/banner.png" />
                      </Field>
                    </div>
                    {form.asset_url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={assetUrl(form.asset_url) || "/placeholder.svg"} alt="Превью креатива" className="mt-2 max-h-24 rounded border border-border object-contain" />
                    ) : null}
                  </details>
                )}
              </>
            ) : null}

            {type === "native" ? (
              <>
                <Field label="Заголовок">
                  <input required maxLength={90} value={form.native_title} onChange={(e) => setForm({ ...form, native_title: e.target.value })} className={inputCls} placeholder="Скидки до 50% на всё" />
                </Field>
                <Field label="Описание">
                  <input maxLength={140} value={form.native_description} onChange={(e) => setForm({ ...form, native_description: e.target.value })} className={inputCls} placeholder="Только до конца недели" />
                </Field>
                <Field label="Кнопка (CTA)">
                  <input value={form.native_cta} onChange={(e) => setForm({ ...form, native_cta: e.target.value })} className={inputCls} />
                </Field>
                <Field label="Изображение" hint="Файл или URL">
                  <div className="flex items-center gap-2">
                    <input type="url" value={form.asset_url} onChange={(e) => setForm({ ...form, asset_url: e.target.value })} className={inputCls} placeholder="https://cdn.site.ru/native.png" />
                    <label className="shrink-0 cursor-pointer rounded-md border border-border bg-surface px-3 py-2 text-xs font-medium text-foreground hover:bg-panel">
                      {uploading === "asset_url" ? "Загрузка…" : "Файл…"}
                      <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" className="sr-only" onChange={(e) => uploadFile("asset_url", e.target.files?.[0] || null)} />
                    </label>
                  </div>
                </Field>
                <Field label="Иконка" hint="Файл или URL">
                  <div className="flex items-center gap-2">
                    <input type="url" value={form.native_icon_url} onChange={(e) => setForm({ ...form, native_icon_url: e.target.value })} className={inputCls} placeholder="https://cdn.site.ru/icon.png" />
                    <label className="shrink-0 cursor-pointer rounded-md border border-border bg-surface px-3 py-2 text-xs font-medium text-foreground hover:bg-panel">
                      {uploading === "native_icon_url" ? "Загрузка…" : "Файл…"}
                      <input type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" onChange={(e) => uploadFile("native_icon_url", e.target.files?.[0] || null)} />
                    </label>
                  </div>
                </Field>
              </>
            ) : null}

            {type === "video" ? (
              <>
                <Field label="Видео (MP4/WebM)" hint="Файл или URL">
                  <div className="flex items-center gap-2">
                    <input type="url" required value={form.video_url} onChange={(e) => setForm({ ...form, video_url: e.target.value })} className={inputCls} placeholder="https://cdn.site.ru/video.mp4" />
                    <label className="shrink-0 cursor-pointer rounded-md border border-border bg-surface px-3 py-2 text-xs font-medium text-foreground hover:bg-panel">
                      {uploading === "video_url" ? "Загрузка…" : "Файл…"}
                      <input type="file" accept="video/mp4,video/webm" className="sr-only" onChange={(e) => uploadFile("video_url", e.target.files?.[0] || null)} />
                    </label>
                  </div>
                </Field>
                <Field label="Длительность, сек">
                  <input type="number" min="5" max="120" value={form.video_duration} onChange={(e) => setForm({ ...form, video_duration: e.target.value })} className={inputCls} />
                </Field>
              </>
            ) : null}
          </div>

          <div className="rounded-md border border-border bg-surface p-3">
            <p className="text-xs font-semibold text-foreground">Информация о рекламодателе</p>
            <p className="mt-0.5 text-xs leading-relaxed text-muted">
              Обязательна по 38-ФЗ: наименование, ОГРН/ИНН и адрес показываются пользователю в инфо-значке на креативе и уходят в ОРД при регистрации erid.
            </p>
            <div className="mt-2">
              <Field label="Контрагент (рекламодатель)">
                <select value={form.counterparty_id} onChange={(e) => setForm({ ...form, counterparty_id: e.target.value })} className={inputCls}>
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
                <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
                  <div className="flex gap-1.5">
                    <dt className="text-muted">Наименование:</dt>
                    <dd className="text-foreground">{selectedCp.name}</dd>
                  </div>
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
                <p className="mt-2 text-xs text-warning">
                  Без контрагента креатив не сможет пройти регистрацию в ОРД. Добавить контрагента: раздел «Маркировка (ОРД)».
                </p>
              )}
            </div>

            <div className="mt-3 border-t border-border pt-3">
              <Field label="Классификатор ККТУ *">
                <KktuSelect
                  value={form.kktu_code}
                  onChange={(code, label) => setForm((f) => ({ ...f, kktu_code: code, kktu_label: label }))}
                />
              </Field>
              {form.kktu_code ? (
                <p className="mt-1.5 text-xs text-muted">
                  Категория: <span className="text-foreground">{kktuShortLabel(form.kktu_code)}</span>
                </p>
              ) : (
                <p className="mt-1.5 text-xs text-muted">
                  Обязательное поле: выберите категорию товара/услуги для маркировки объявления. Код уходит в ОРД вместе с erid.
                </p>
              )}
            </div>
          </div>

          <div className="rounded-md border border-border bg-surface p-3">
            <p className="mb-2 text-xs font-semibold text-foreground">Превью: так увидит пользователь</p>
            <CreativePreview
              type={type}
              assetUrl={assetUrl(form.asset_url) || null}
              width={Number(form.size.split("x")[0]) || 300}
              height={Number(form.size.split("x")[1]) || 250}
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

          {error ? <p className="text-xs text-danger">{error}</p> : null}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? "Сохранение…" : "Сохранить"}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  )
}
