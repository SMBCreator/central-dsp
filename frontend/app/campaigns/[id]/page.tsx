"use client"

import { use, useState } from "react"
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
type BlockRow = {
  id: string
  block_type: string
  block_value: string
  reason: string
  partner_name?: string | null
  created_at: string
}

function OptimizationCard({ campaign }: { campaign: Campaign }) {
  const { data: pixels } = useSWR<PixelRow[]>("/pixels", fetcher)
  const { data: blocks } = useSWR<BlockRow[]>(`/campaigns/${campaign.id}/blocks`, fetcher)
  const c = campaign as Campaign & {
    pixel_id?: string | null
    goal_id?: string | null
    target_cpa?: number | null
    target_cpc?: number | null
    auto_optimize?: boolean
  }
  const [pixelId, setPixelId] = useState(c.pixel_id || "")
  const { data: goals } = useSWR<GoalRow[]>(pixelId ? `/pixels/${pixelId}/goals` : null, fetcher)
  const [goalId, setGoalId] = useState(c.goal_id || "")
  const [cpa, setCpa] = useState(c.target_cpa ? String(c.target_cpa) : "")
  const [cpc, setCpc] = useState(c.target_cpc ? String(c.target_cpc) : "")
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
  }

  return (
    <Card>
      <CardHeader
        title="Оптимизация закупки"
        subtitle="Пиксель, цель и автоматическая отсечка слабых источников"
      />
      <div className="space-y-4 p-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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
        </div>
        <label className="flex cursor-pointer items-center gap-2.5">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} className="h-4 w-4 rounded border-border accent-current" />
          <span className="text-sm text-foreground">Автооптимизация</span>
          <span className="text-xs text-muted">— ежечасно блокировать площадки со скликиванием, нулевой вовлечённостью и дорогими кликами/конверсиями</span>
        </label>
        <div className="flex items-center gap-3">
          <Button onClick={saveOpt} disabled={saving}>
            {saving ? "Сохранение…" : "Сохранить настройки"}
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

export default function CampaignDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const { data: campaign } = useSWR<Campaign>(`/campaigns/${id}`, fetcher)
  const { data: lineItems } = useSWR<LineItem[]>(`/campaigns/${id}/line-items`, fetcher)
  const { data: stats } = useSWR<StatsSummary>(`/stats/summary?hours=168&campaign_id=${id}`, fetcher)

  const [open, setOpen] = useState(false)
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

  const openCreate = () => {
    setError(null)
    setForm({ name: "", bid_type: "cpm", bid_price: "1.5", max_bid: "", daily_budget: "20" })
    setTargeting({})
    setOpen(true)
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      await api(`/campaigns/${id}/line-items`, {
        method: "POST",
        body: {
          name: form.name,
          bid_type: form.bid_type,
          bid_price: Number(form.bid_price),
          max_bid: form.max_bid ? Number(form.max_bid) : null,
          daily_budget: Number(form.daily_budget),
          targeting,
        },
      })
      await mutate(`/campaigns/${id}/line-items`)
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
    await mutate(`/campaigns/${id}/line-items`)
  }

  if (!campaign) return <Spinner />

  return (
    <div className="space-y-6">
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

      {stats ? (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <KpiCard label="Расход (7 дней)" value={fmtMoney(stats.spend)} />
          <KpiCard label="Показы" value={fmtNum(stats.impressions)} />
          <KpiCard label="Клики" value={fmtNum(stats.clicks)} hint={`CTR ${fmtPct(stats.ctr)}`} />
          <KpiCard label="Win Rate" value={fmtPct(stats.win_rate)} />
        </div>
      ) : null}

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
                    <div className="flex justify-end">
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

      <Modal open={open} onClose={() => setOpen(false)} title="Новая строка закупки" wide>
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
              {saving ? "Создание…" : "Создать"}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  )
}
