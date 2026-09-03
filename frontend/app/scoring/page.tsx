"use client"

import { useMemo, useState } from "react"
import useSWR, { mutate } from "swr"
import { api, fetcher } from "@/lib/api"
import type { ScoringProfile } from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Field, inputCls, Modal, Spinner, StatusBadge } from "@/components/ui"
import { ScoringReportCard } from "@/components/scoring-report"

type Pixel = { id: string; name: string }

const EMPTY: Partial<ScoringProfile> = {
  name: "",
  description: "",
  window_days: 7,
  min_impressions: 200,
  min_clicks: 0,
  min_ctr: null,
  min_viewability: null,
  max_bounce_rate: null,
  min_avg_depth: null,
  min_time_on_site: null,
  max_cpa: null,
  pixel_id: null,
  block_threshold: 0.5,
  enforce: false,
}

// Готовые пресеты порогов — быстрый старт под типовые задачи.
const PRESETS: { key: string; label: string; hint: string; patch: Partial<ScoringProfile> }[] = [
  {
    key: "brand",
    label: "Бренд-безопасность",
    hint: "Премиальный инвентарь: высокий viewability и живое поведение.",
    patch: { window_days: 14, min_impressions: 300, min_ctr: 0.08, min_viewability: 60, max_bounce_rate: 65, min_avg_depth: 1.8, min_time_on_site: 25, block_threshold: 0.5, enforce: true },
  },
  {
    key: "antifraud",
    label: "Антифрод (клик-фермы)",
    hint: "Отсекает аномальный CTR и нулевой viewability.",
    patch: { window_days: 7, min_impressions: 500, min_ctr: 0.03, min_viewability: 40, max_bounce_rate: 85, block_threshold: 0.5, enforce: true },
  },
  {
    key: "cpa",
    label: "Перформанс по CPA",
    hint: "Блокирует площадки с дорогой конверсией по цели.",
    patch: { window_days: 7, min_impressions: 200, min_clicks: 5, max_cpa: 1500, block_threshold: 0.5, enforce: false },
  },
]

function numOrNull(v: string): number | null {
  if (v === "" || v === null) return null
  const n = Number(v)
  return Number.isNaN(n) ? null : n
}

export default function ScoringPage() {
  const { data: profiles, isLoading } = useSWR<ScoringProfile[]>("/scoring/profiles", fetcher)
  const { data: pixels } = useSWR<Pixel[]>("/pixels", fetcher)

  const [selected, setSelected] = useState<string | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<ScoringProfile | null>(null)
  const [form, setForm] = useState<Partial<ScoringProfile>>(EMPTY)
  const [saving, setSaving] = useState(false)

  const activeProfileId = selected ?? profiles?.[0]?.id ?? null
  const activeProfile = useMemo(
    () => (profiles || []).find((p) => p.id === activeProfileId) ?? null,
    [profiles, activeProfileId],
  )

  const enforcedCount = useMemo(() => (profiles || []).filter((p) => p.enforce).length, [profiles])

  const openCreate = () => {
    setEditing(null)
    setForm(EMPTY)
    setModalOpen(true)
  }
  const openEdit = (p: ScoringProfile) => {
    setEditing(p)
    setForm({ ...p })
    setModalOpen(true)
  }

  const applyPreset = (patch: Partial<ScoringProfile>) => setForm((f) => ({ ...f, ...patch }))

  const save = async () => {
    if (!form.name?.trim()) return
    setSaving(true)
    try {
      const body = {
        name: form.name,
        description: form.description || null,
        window_days: Number(form.window_days) || 7,
        min_impressions: Number(form.min_impressions) || 0,
        min_clicks: Number(form.min_clicks) || 0,
        min_ctr: form.min_ctr,
        min_viewability: form.min_viewability,
        max_bounce_rate: form.max_bounce_rate,
        min_avg_depth: form.min_avg_depth,
        min_time_on_site: form.min_time_on_site,
        max_cpa: form.max_cpa,
        pixel_id: form.pixel_id || null,
        block_threshold: Number(form.block_threshold) || 0.5,
        enforce: !!form.enforce,
      }
      if (editing?.id) {
        await api(`/scoring/profiles/${editing.id}`, { method: "PATCH", body })
      } else {
        await api("/scoring/profiles", { method: "POST", body })
      }
      await mutate("/scoring/profiles")
      setModalOpen(false)
    } finally {
      setSaving(false)
    }
  }

  const remove = async (p: ScoringProfile) => {
    if (!p.id) return
    await api(`/scoring/profiles/${p.id}`, { method: "DELETE" })
    await mutate("/scoring/profiles")
    if (selected === p.id) setSelected(null)
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Скоринг качества площадок</h1>
          <p className="mt-0.5 text-sm text-muted text-pretty">
            Правила качества по площадкам и SSP: CTR, viewability, поведение из пикселя и CPA. Профили привязываются к
            кампании или группе и в режиме «Блокировать» исключают слабый инвентарь из закупки.
          </p>
        </div>
        <Button onClick={openCreate}>Создать профиль</Button>
      </div>

      <div className="flex flex-col gap-6">
        <Card>
          <CardHeader
            title="Профили скоринга"
            subtitle={
              profiles?.length ? `${profiles.length} профилей · ${enforcedCount} блокируют трафик` : "Наборы правил качества"
            }
            action={
              activeProfile ? (
                <div className="flex gap-2">
                  <Button variant="secondary" onClick={() => openEdit(activeProfile)}>
                    Изменить
                  </Button>
                  <Button variant="danger" onClick={() => remove(activeProfile)}>
                    Удалить
                  </Button>
                </div>
              ) : undefined
            }
          />
          {isLoading ? (
            <Spinner />
          ) : !profiles?.length ? (
            <EmptyState
              title="Нет профилей"
              description="Создайте профиль качества, чтобы оценивать площадки и отсекать слабый инвентарь."
              action={<Button onClick={openCreate}>Создать профиль</Button>}
            />
          ) : (
            <ul className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-3" role="radiogroup" aria-label="Выбор профиля">
              {profiles.map((p) => {
                const active = p.id === activeProfileId
                return (
                  <li key={p.id}>
                    <button
                      type="button"
                      role="radio"
                      aria-checked={active}
                      onClick={() => setSelected(p.id)}
                      className={`flex h-full w-full flex-col gap-1.5 rounded-lg border px-3.5 py-3 text-left transition-colors ${
                        active
                          ? "border-accent bg-accent/5 ring-1 ring-accent/40"
                          : "border-border bg-card hover:bg-panel/60"
                      }`}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium text-foreground">{p.name}</span>
                        <StatusBadge status={p.enforce ? "active" : "paused"} />
                      </span>
                      {p.description ? (
                        <span className="line-clamp-2 text-xs text-muted text-pretty">{p.description}</span>
                      ) : null}
                      <span className="mt-auto flex flex-wrap gap-1 pt-0.5 text-[10px] text-muted">
                        <span className="rounded bg-panel px-1.5 py-0.5">окно {p.window_days} дн</span>
                        {p.min_ctr !== null ? <span className="rounded bg-panel px-1.5 py-0.5">CTR ≥ {p.min_ctr}%</span> : null}
                        {p.min_viewability !== null ? (
                          <span className="rounded bg-panel px-1.5 py-0.5">view ≥ {p.min_viewability}%</span>
                        ) : null}
                        {p.max_cpa !== null ? <span className="rounded bg-panel px-1.5 py-0.5">CPA ≤ {p.max_cpa}₽</span> : null}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </Card>

        <ScoringReportCard profileId={activeProfileId} />
      </div>

      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title={editing ? "Профиль скоринга" : "Новый профиль скоринга"} wide>
        <div className="space-y-4">
          {!editing ? (
            <div>
              <p className="mb-1.5 text-xs font-medium text-foreground">Быстрый старт</p>
              <div className="flex flex-wrap gap-2">
                {PRESETS.map((preset) => (
                  <button
                    key={preset.key}
                    type="button"
                    title={preset.hint}
                    onClick={() => applyPreset(preset.patch)}
                    className="rounded-lg border border-border bg-card px-3 py-1.5 text-xs text-foreground transition-colors hover:bg-panel"
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          <Field label="Название">
            <input
              className={inputCls}
              value={form.name || ""}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="Например: Бренд-безопасность"
            />
          </Field>
          <Field label="Описание">
            <input
              className={inputCls}
              value={form.description || ""}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              placeholder="Кратко: для чего профиль"
            />
          </Field>

          <div className="grid grid-cols-3 gap-3">
            <Field label="Окно, дней" hint="За какой период агрегируется статистика площадки.">
              <input
                type="number"
                className={inputCls}
                value={form.window_days ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, window_days: numOrNull(e.target.value) ?? 7 }))}
              />
            </Field>
            <Field label="Мин. показов" hint="Меньше — данных недостаточно, площадка не блокируется.">
              <input
                type="number"
                className={inputCls}
                value={form.min_impressions ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, min_impressions: numOrNull(e.target.value) ?? 0 }))}
              />
            </Field>
            <Field label="Мин. кликов">
              <input
                type="number"
                className={inputCls}
                value={form.min_clicks ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, min_clicks: numOrNull(e.target.value) ?? 0 }))}
              />
            </Field>
          </div>

          <p className="text-xs font-medium text-foreground">Пороги качества (пусто = правило выключено)</p>
          <div className="grid grid-cols-3 gap-3">
            <Field label="CTR ≥, %">
              <input type="number" step="0.01" className={inputCls} value={form.min_ctr ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, min_ctr: numOrNull(e.target.value) }))} />
            </Field>
            <Field label="Viewability ≥, %">
              <input type="number" step="1" className={inputCls} value={form.min_viewability ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, min_viewability: numOrNull(e.target.value) }))} />
            </Field>
            <Field label="Отказы ≤, %" hint="Из пикселя: доля сессий-отказов.">
              <input type="number" step="1" className={inputCls} value={form.max_bounce_rate ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, max_bounce_rate: numOrNull(e.target.value) }))} />
            </Field>
            <Field label="Глубина ≥, стр." hint="Из пикселя: страниц за сессию.">
              <input type="number" step="0.1" className={inputCls} value={form.min_avg_depth ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, min_avg_depth: numOrNull(e.target.value) }))} />
            </Field>
            <Field label="Время ≥, сек" hint="Из пикселя: среднее время на сайте.">
              <input type="number" step="1" className={inputCls} value={form.min_time_on_site ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, min_time_on_site: numOrNull(e.target.value) }))} />
            </Field>
            <Field label="CPA ≤, ₽" hint="Из пикселя: цена конверсии по цели.">
              <input type="number" step="1" className={inputCls} value={form.max_cpa ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, max_cpa: numOrNull(e.target.value) }))} />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Пиксель" hint="Источник поведенческих метрик и конверсий.">
              <select
                className={inputCls}
                value={form.pixel_id || ""}
                onChange={(e) => setForm((f) => ({ ...f, pixel_id: e.target.value || null }))}
              >
                <option value="">— не использовать —</option>
                {(pixels || []).map((px) => (
                  <option key={px.id} value={px.id}>
                    {px.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Порог блокировки" hint="Итоговый score ниже этого значения = площадка в блок-лист.">
              <input type="number" step="0.05" min="0" max="1" className={inputCls} value={form.block_threshold ?? 0.5}
                onChange={(e) => setForm((f) => ({ ...f, block_threshold: numOrNull(e.target.value) ?? 0.5 }))} />
            </Field>
          </div>

          <label className="flex items-start gap-2.5 rounded-lg border border-border bg-panel/40 px-3 py-2.5">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={!!form.enforce}
              onChange={(e) => setForm((f) => ({ ...f, enforce: e.target.checked }))}
            />
            <span className="text-xs text-foreground">
              <span className="font-medium">Блокировать закупку</span>
              <span className="block text-muted text-pretty">
                Площадки ниже порога исключаются из ставок для привязанных кампаний. Выключено — только отчёт (наблюдение).
              </span>
            </span>
          </label>

          <div className="flex justify-end gap-2 pt-1">
            <Button variant="secondary" onClick={() => setModalOpen(false)}>
              Отмена
            </Button>
            <Button onClick={save} disabled={saving || !form.name?.trim()}>
              {saving ? "Сохранение…" : editing ? "Сохранить" : "Создать"}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
