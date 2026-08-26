"use client"

import { use, useEffect, useState } from "react"
import Link from "next/link"
import useSWR, { mutate } from "swr"
import { api, fetcher } from "@/lib/api"
import {
  AD_TYPE_LABELS,
  fmtMoney,
  fmtNum,
  fmtPct,
  type Campaign,
  type LineItem,
  type StatsSummary,
  type Targeting,
} from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Field, inputCls, KpiCard, Modal, Spinner, StatusBadge, Table } from "@/components/ui"
import { DEVICES, TargetingEditor } from "@/components/targeting-editor"

type PixelRow = { id: string; name: string; domain: string }
type GoalRow = { id: string; name: string; goal_type: string; match_value: string }
type Group = { id: string; name: string }
type BlockRow = {
  id: string
  block_type: string
  block_value: string
  reason: string
  partner_name?: string | null
  created_at: string
}
type CampaignExt = Campaign & {
  group_id?: string | null
  pacing?: string
  bid_shading?: boolean
  frequency_caps?: { impressions: number; period_hours: number }[] | null
  start_date?: string | null
  end_date?: string | null
  objective: string
}

const OBJECTIVES = [
  { value: "awareness", label: "Охват" },
  { value: "traffic", label: "Трафик" },
  { value: "conversions", label: "Конверсии" },
]

/* ---------- Основные настройки кампании ---------- */

function SettingsCard({ campaign }: { campaign: CampaignExt }) {
  const { data: groups } = useSWR<Group[]>("/groups", fetcher)
  const [name, setName] = useState(campaign.name)
  const [objective, setObjective] = useState(campaign.objective)
  const [groupId, setGroupId] = useState(campaign.group_id || "")
  const [dailyBudget, setDailyBudget] = useState(String(campaign.daily_budget ?? ""))
  const [totalBudget, setTotalBudget] = useState(campaign.total_budget ? String(campaign.total_budget) : "")
  const [startDate, setStartDate] = useState(campaign.start_date || "")
  const [endDate, setEndDate] = useState(campaign.end_date || "")
  const [pacing, setPacing] = useState(campaign.pacing || "even")
  const [bidShading, setBidShading] = useState(campaign.bid_shading !== false)
  const [freqCaps, setFreqCaps] = useState<{ imps: string; hours: string }[]>(
    (campaign.frequency_caps || []).map((w) => ({ imps: String(w.impressions), hours: String(w.period_hours) })),
  )
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Синхронизация при обновлении кампании после сохранения
  useEffect(() => {
    setName(campaign.name)
    setObjective(campaign.objective)
    setGroupId(campaign.group_id || "")
  }, [campaign.name, campaign.objective, campaign.group_id])

  const save = async () => {
    setSaving(true)
    setSaved(false)
    setError(null)
    try {
      const caps = freqCaps
        .filter((w) => w.imps && Number(w.imps) > 0)
        .map((w) => ({ impressions: Number(w.imps), period_hours: Number(w.hours) }))
      await api(`/campaigns/${campaign.id}`, {
        method: "PATCH",
        body: {
          name: name.trim(),
          objective,
          group_id: groupId || null,
          daily_budget: dailyBudget ? Number(dailyBudget) : undefined,
          total_budget: totalBudget ? Number(totalBudget) : undefined,
          start_date: startDate || null,
          end_date: endDate || null,
          pacing,
          bid_shading: bidShading,
          frequency_caps: caps,
        },
      })
      await mutate(`/campaigns/${campaign.id}`)
      await mutate("/campaigns")
      setSaved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка сохранения")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader title="Настройки кампании" subtitle="Название, бюджеты, сроки, частотные капы" />
      <div className="space-y-4 p-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Название кампании">
            <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} />
          </Field>
          <Field label="Цель">
            <select value={objective} onChange={(e) => setObjective(e.target.value)} className={inputCls}>
              {OBJECTIVES.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Группа кампаний">
            <select value={groupId} onChange={(e) => setGroupId(e.target.value)} className={inputCls}>
              <option value="">Без группы</option>
              {(groups || []).map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Дневной бюджет, USD">
            <input type="number" min="1" step="1" value={dailyBudget} onChange={(e) => setDailyBudget(e.target.value)} className={inputCls} />
          </Field>
          <Field label="Общий бюджет, USD">
            <input type="number" min="0" step="1" value={totalBudget} onChange={(e) => setTotalBudget(e.target.value)} className={inputCls} placeholder="Без ограничения" />
          </Field>
          <Field label="Темп расходования">
            <select value={pacing} onChange={(e) => setPacing(e.target.value)} className={inputCls}>
              <option value="even">Равномерно в течение дня</option>
              <option value="asap">Как можно быстрее (ASAP)</option>
            </select>
          </Field>
          <Field label="Дата старта" hint="Пусто — сразу">
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className={inputCls} />
          </Field>
          <Field label="Дата окончания" hint="Пусто — без ограничения">
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className={inputCls} />
          </Field>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-foreground">Частотные капы</span>
            {freqCaps.length < 3 ? (
              <button
                type="button"
                onClick={() => setFreqCaps([...freqCaps, { imps: "", hours: freqCaps.length === 0 ? "24" : "168" }])}
                className="text-xs font-medium text-accent hover:underline"
              >
                + Добавить окно
              </button>
            ) : null}
          </div>
          {freqCaps.length === 0 ? (
            <p className="text-xs text-muted">{"Без лимита. Добавьте до 3 окон — например, 3 показа в день и 10 в неделю."}</p>
          ) : null}
          {freqCaps.map((w, i) => (
            <div key={i} className="flex max-w-md items-end gap-2">
              <div className="flex-1">
                <label className="mb-1 block text-xs text-muted">Показов</label>
                <input
                  type="number"
                  min="1"
                  max="1000"
                  value={w.imps}
                  onChange={(e) => setFreqCaps(freqCaps.map((x, j) => (j === i ? { ...x, imps: e.target.value } : x)))}
                  className={inputCls}
                  placeholder="3"
                />
              </div>
              <div className="flex-1">
                <label className="mb-1 block text-xs text-muted">Период</label>
                <select
                  value={w.hours}
                  onChange={(e) => setFreqCaps(freqCaps.map((x, j) => (j === i ? { ...x, hours: e.target.value } : x)))}
                  className={inputCls}
                >
                  <option value="1">Час</option>
                  <option value="24">Сутки</option>
                  <option value="168">Неделя</option>
                  <option value="720">Месяц</option>
                </select>
              </div>
              <Button variant="ghost" onClick={() => setFreqCaps(freqCaps.filter((_, j) => j !== i))}>
                Удалить
              </Button>
            </div>
          ))}
        </div>

        <label className="flex cursor-pointer items-start gap-2.5">
          <input
            type="checkbox"
            checked={bidShading}
            onChange={(e) => setBidShading(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-border accent-current"
          />
          <span className="text-sm text-foreground">
            Bid-шейдинг
            <span className="block text-xs text-muted">
              {"Автоматически снижает ставку ближе к цене клиринга (first-price). Экономия 15–30% без потери объёма."}
            </span>
          </span>
        </label>

        {error ? <p className="text-xs text-danger">{error}</p> : null}
        <div className="flex items-center gap-3">
          <Button onClick={save} disabled={saving}>
            {saving ? "Сохранение…" : "Сохранить настройки"}
          </Button>
          {saved ? <span className="font-mono text-[11px] text-success">Сохранено</span> : null}
        </div>
      </div>
    </Card>
  )
}

/* ---------- Оптимизация закупки ---------- */

function OptimizationCard({ campaign }: { campaign: Campaign }) {
  const { data: pixels } = useSWR<PixelRow[]>("/pixels", fetcher)
  const { data: blocks } = useSWR<BlockRow[]>(`/campaigns/${campaign.id}/blocks`, fetcher)
  const c = campaign as Campaign & {
    pixel_id?: string | null
    goal_id?: string | null
    target_cpa?: number | null
    target_cpc?: number | null
    target_cpm?: number | null
    target_cpv?: number | null
    auto_optimize?: boolean
  }
  const [pixelId, setPixelId] = useState(c.pixel_id || "")
  const { data: goals } = useSWR<GoalRow[]>(pixelId ? `/pixels/${pixelId}/goals` : null, fetcher)
  const [goalId, setGoalId] = useState(c.goal_id || "")
  const [cpa, setCpa] = useState(c.target_cpa ? String(c.target_cpa) : "")
  const [cpc, setCpc] = useState(c.target_cpc ? String(c.target_cpc) : "")
  const [cpm, setCpm] = useState(c.target_cpm ? String(c.target_cpm) : "")
  const [cpv, setCpv] = useState(c.target_cpv ? String(c.target_cpv) : "")
  const [auto, setAuto] = useState(!!c.auto_optimize)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const saveOpt = async () => {
    setSaving(true)
    setSaved(false)
    try {
      await api(`/campaigns/${campaign.id}`, {
        method: "PATCH",
        body: {
          pixel_id: pixelId || null,
          goal_id: goalId || null,
          target_cpa: cpa ? Number(cpa) : null,
          target_cpc: cpc ? Number(cpc) : null,
          target_cpm: cpm ? Number(cpm) : null,
          target_cpv: cpv ? Number(cpv) : null,
          auto_optimize: auto,
        },
      })
      await mutate(`/campaigns/${campaign.id}`)
      setSaved(true)
    } finally {
      setSaving(false)
    }
  }

  const unblock = async (blockId: string) => {
    await api(`/campaigns/${campaign.id}/blocks/${blockId}`, { method: "DELETE" })
    await mutate(`/campaigns/${campaign.id}/blocks`)
  }

  const BLOCK_TYPE_LABELS: Record<string, string> = {
    domain: "Домен",
    supply_partner: "SSP",
    device_type: "Устройство",
    geo_country: "Гео",
    os: "ОС",
    browser: "Браузер",
    ad_type: "Формат",
    creative: "Креатив",
    line_item: "Строка закупки",
    hour: "Час (UTC)",
  }

  return (
    <Card>
      <CardHeader
        title="Оптимизация закупки"
        subtitle="Пиксель, цель и автоматическая отсечка слабых источников"
      />
      <div className="space-y-4 p-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Пиксель" hint={pixels && pixels.length === 0 ? "Создайте пиксель на странице «Пиксель и цели»" : "Куда придут конверсии"}>
            <select value={pixelId} onChange={(e) => { setPixelId(e.target.value); setGoalId("") }} className={inputCls}>
              <option value="">Без пикселя</option>
              {(pixels || []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.domain})
                </option>
              ))}
            </select>
          </Field>
          <Field label="Цель" hint="Конверсия для оптимизации">
            <select value={goalId} onChange={(e) => setGoalId(e.target.value)} className={inputCls} disabled={!pixelId}>
              <option value="">Все конверсии пикселя</option>
              {(goals || []).map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Целевой CPA, USD" hint="Максимальная цена конверсии">
            <input type="number" min="0" step="0.01" value={cpa} onChange={(e) => setCpa(e.target.value)} className={inputCls} placeholder="Не задан" />
          </Field>
          <Field label="Целевой CPC, USD" hint="Максимальная цена клика">
            <input type="number" min="0" step="0.001" value={cpc} onChange={(e) => setCpc(e.target.value)} className={inputCls} placeholder="Не задан" />
          </Field>
          <Field label="Целевой CPM, USD" hint="Максимальная цена 1000 показов">
            <input type="number" min="0" step="0.01" value={cpm} onChange={(e) => setCpm(e.target.value)} className={inputCls} placeholder="Не задан" />
          </Field>
          <Field label="Целевой CPV, USD" hint="Максимальная цена досмотра видео">
            <input type="number" min="0" step="0.001" value={cpv} onChange={(e) => setCpv(e.target.value)} className={inputCls} placeholder="Не задан" />
          </Field>
        </div>
        <label className="flex cursor-pointer items-center gap-2.5">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} className="h-4 w-4 rounded border-border accent-current" />
          <span className="text-sm text-foreground">Автооптимизация</span>
          <span className="text-xs text-muted">— ежечасно блокировать площадки со скликиванием, нулевой вовлечённостью и дорогими кликами/конверсиями</span>
        </label>
        <div className="flex items-center gap-3">
          <Button onClick={saveOpt} disabled={saving}>
            {saving ? "Сохранение…" : "Сохранить оптимизацию"}
          </Button>
          {saved ? <span className="font-mono text-[11px] text-success">Сохранено</span> : null}
        </div>

        {blocks && blocks.length > 0 ? (
          <div className="border-t border-border pt-4">
            <p className="mb-2 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
              Заблокировано оптимизатором ({blocks.length})
            </p>
            <div className="space-y-2">
              {blocks.map((b) => (
                <div key={b.id} className="flex flex-wrap items-center justify-between gap-2 rounded-2xl bg-panel px-4 py-2.5">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-foreground">
                      {BLOCK_TYPE_LABELS[b.block_type] || b.block_type}: {b.partner_name || b.block_value}
                    </p>
                    <p className="mt-0.5 text-xs text-muted">{b.reason}</p>
                  </div>
                  <Button variant="ghost" onClick={() => unblock(b.id)}>
                    Разблокировать
                  </Button>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </Card>
  )
}

function targetingSummary(t: Targeting): string {
  const parts = [
    t.geo_countries?.join(", "),
    t.device_types?.map((d) => DEVICES.find((x) => x.value === d)?.label || d).join(", "),
    t.ad_types?.map((x) => AD_TYPE_LABELS[x] || x).join(", "),
    t.iab_categories?.length ? `IAB: ${t.iab_categories.length}` : undefined,
    t.age_min || t.age_max ? `Возраст ${t.age_min ?? "?"}–${t.age_max ?? "?"}` : undefined,
    t.hours?.length ? `Часы: ${t.hours.length}` : undefined,
  ]
  return parts.filter(Boolean).join(" · ") || "Без ограничений"
}

/* ---------- Страница ---------- */

export default function CampaignDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const { data: campaign } = useSWR<CampaignExt>(`/campaigns/${id}`, fetcher)
  const { data: lineItems } = useSWR<LineItem[]>(`/line-items?campaign_id=${id}`, fetcher)
  const { data: stats } = useSWR<StatsSummary>(`/stats/summary?hours=168&campaign_id=${id}`, fetcher)

  const [open, setOpen] = useState(false)
  const [editingLi, setEditingLi] = useState<LineItem | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [targeting, setTargeting] = useState<Targeting>({})
  const [form, setForm] = useState({
    name: "",
    bid_type: "cpm",
    bid_price: "1.5",
    max_bid: "",
    daily_budget: "20",
  })
  const [statusError, setStatusError] = useState<string | null>(null)

  const openCreate = () => {
    setError(null)
    setEditingLi(null)
    setForm({ name: "", bid_type: "cpm", bid_price: "1.5", max_bid: "", daily_budget: "20" })
    setTargeting({})
    setOpen(true)
  }

  const openEdit = (li: LineItem) => {
    setError(null)
    setEditingLi(li)
    setForm({
      name: li.name,
      bid_type: li.bid_type,
      bid_price: String(li.bid_price),
      max_bid: li.max_bid ? String(li.max_bid) : "",
      daily_budget: String(li.daily_budget),
    })
    setTargeting(li.targeting || {})
    setOpen(true)
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const body = {
        name: form.name,
        bid_type: form.bid_type,
        bid_price: Number(form.bid_price),
        max_bid: form.max_bid ? Number(form.max_bid) : null,
        daily_budget: Number(form.daily_budget),
        targeting,
      }
      if (editingLi) {
        await api(`/line-items/${editingLi.id}`, { method: "PATCH", body })
      } else {
        await api(`/line-items`, { method: "POST", body: { campaign_id: id, ...body } })
      }
      await mutate(`/line-items?campaign_id=${id}`)
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка сохранения")
    } finally {
      setSaving(false)
    }
  }

  const toggleLi = async (li: LineItem) => {
    await api(`/line-items/${li.id}`, {
      method: "PATCH",
      body: { status: li.status === "active" ? "paused" : "active" },
    })
    await mutate(`/line-items?campaign_id=${id}`)
  }

  const toggleCampaign = async () => {
    if (!campaign) return
    setStatusError(null)
    try {
      await api(`/campaigns/${id}`, {
        method: "PATCH",
        body: { status: campaign.status === "active" ? "paused" : "active" },
      })
      await mutate(`/campaigns/${id}`)
    } catch (err) {
      setStatusError(err instanceof Error ? err.message : "Не удалось изменить статус")
    }
  }

  if (!campaign) return <Spinner />

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/campaigns" className="text-xs text-muted hover:text-accent">
            ← Все кампании
          </Link>
          <div className="mt-1 flex flex-wrap items-center gap-3">
            <h1 className="text-xl font-semibold text-foreground">{campaign.name}</h1>
            <StatusBadge status={campaign.status} />
          </div>
          <p className="mt-0.5 text-sm text-muted">
            Дневной бюджет {fmtMoney(campaign.daily_budget)} · Общий {fmtMoney(campaign.total_budget)} · Потрачено{" "}
            {fmtMoney(campaign.spent_total)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={toggleCampaign}>
            {campaign.status === "active" ? "Пауза" : "Запустить"}
          </Button>
          <Link href={`/analytics?campaign_id=${id}`}>
            <Button variant="secondary">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M4 20h16M7 16v-5m5 5V8m5 8v-3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
              Статистика
            </Button>
          </Link>
        </div>
      </div>

      {statusError ? (
        <p role="alert" className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
          {statusError}
        </p>
      ) : null}

      {stats ? (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <KpiCard label="Расход (7 дней)" value={fmtMoney(stats.spend)} />
          <KpiCard label="Показы" value={fmtNum(stats.impressions)} />
          <KpiCard label="Клики" value={fmtNum(stats.clicks)} hint={`CTR ${fmtPct(stats.ctr)}`} />
          <KpiCard label="Win Rate" value={fmtPct(stats.win_rate)} />
        </div>
      ) : null}

      <SettingsCard campaign={campaign} />

      <OptimizationCard campaign={campaign} />

      <Card>
        <CardHeader
          title="Строки закупки (Line Items)"
          subtitle="Ставки и таргетинг внутри кампании"
          action={<Button onClick={openCreate}>Добавить строку</Button>}
        />
        {lineItems ? (
          lineItems.length > 0 ? (
            <Table headers={["Название", "Статус", "Ставка", "Дневной бюджет", "Расход", "Таргетинг", ""]}>
              {lineItems.map((li) => (
                <tr key={li.id} className="hover:bg-surface">
                  <td className="px-4 py-3 font-medium text-foreground">{li.name}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={li.status} />
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">
                    {fmtMoney(li.bid_price)} {li.bid_type.toUpperCase()}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtMoney(li.daily_budget)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtMoney(li.spent_today)}</td>
                  <td className="max-w-[280px] px-4 py-3 text-xs text-muted">{targetingSummary(li.targeting)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1.5">
                      <Button variant="ghost" onClick={() => openEdit(li)}>
                        Изменить
                      </Button>
                      <Button variant="ghost" onClick={() => toggleLi(li)}>
                        {li.status === "active" ? "Пауза" : "Запустить"}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </Table>
          ) : (
            <EmptyState
              title="Нет строк закупки"
              description="Добавьте строку со ставкой и таргетингом — без неё кампания не участвует в аукционах."
              action={<Button onClick={openCreate}>Добавить строку</Button>}
            />
          )
        ) : (
          <Spinner />
        )}
      </Card>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={editingLi ? `Строка закупки: ${editingLi.name}` : "Новая строка закупки"}
        wide
      >
        <form onSubmit={save} className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Название">
              <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={inputCls} placeholder="RU мобайл баннеры" />
            </Field>
            <Field label="Тип ставки">
              <select value={form.bid_type} onChange={(e) => setForm({ ...form, bid_type: e.target.value })} className={inputCls}>
                <option value="cpm">CPM (за 1000 показов)</option>
                <option value="cpc">CPC (за клик)</option>
              </select>
            </Field>
            <Field label="Ставка, USD">
              <input type="number" required min="0.01" step="0.01" value={form.bid_price} onChange={(e) => setForm({ ...form, bid_price: e.target.value })} className={inputCls} />
            </Field>
            <Field label="Макс. ставка, USD" hint="Для автооптимизатора">
              <input type="number" min="0.01" step="0.01" value={form.max_bid} onChange={(e) => setForm({ ...form, max_bid: e.target.value })} className={inputCls} />
            </Field>
            <Field label="Дневной бюджет, USD">
              <input type="number" required min="0.01" step="0.01" value={form.daily_budget} onChange={(e) => setForm({ ...form, daily_budget: e.target.value })} className={inputCls} />
            </Field>
          </div>

          <TargetingEditor value={targeting} onChange={setTargeting} />

          {error ? <p className="text-xs text-danger">{error}</p> : null}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? "Сохранение…" : editingLi ? "Сохранить" : "Создать"}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  )
}
