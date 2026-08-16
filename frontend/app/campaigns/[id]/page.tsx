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
} from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Field, inputCls, KpiCard, Modal, Spinner, StatusBadge, Table } from "@/components/ui"

const COUNTRIES = ["RU", "KZ", "BY", "UZ", "KG", "AM", "AZ", "GE", "MD", "TJ"]
const DEVICES = [
  { value: "mobile", label: "Мобильные" },
  { value: "desktop", label: "Десктоп" },
  { value: "tablet", label: "Планшеты" },
  { value: "ctv", label: "Smart TV" },
]

export default function CampaignDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const { data: campaign } = useSWR<Campaign>(`/campaigns/${id}`, fetcher)
  const { data: lineItems } = useSWR<LineItem[]>(`/campaigns/${id}/line-items`, fetcher)
  const { data: stats } = useSWR<StatsSummary>(`/stats/summary?hours=168&campaign_id=${id}`, fetcher)

  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({
    name: "",
    bid_type: "cpm",
    bid_price: "1.5",
    max_bid: "",
    daily_budget: "20",
    geo: [] as string[],
    devices: [] as string[],
    adTypes: [] as string[],
  })

  const openCreate = () => {
    setError(null)
    setForm({ name: "", bid_type: "cpm", bid_price: "1.5", max_bid: "", daily_budget: "20", geo: [], devices: [], adTypes: [] })
    setOpen(true)
  }

  const toggleArr = (arr: string[], v: string) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v])

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
          targeting: {
            geo_countries: form.geo.length ? form.geo : undefined,
            device_types: form.devices.length ? form.devices : undefined,
            ad_types: form.adTypes.length ? form.adTypes : undefined,
          },
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
                  <td className="px-4 py-3 text-xs text-muted">
                    {[
                      li.targeting.geo_countries?.join(", "),
                      li.targeting.device_types?.map((d) => DEVICES.find((x) => x.value === d)?.label || d).join(", "),
                      li.targeting.ad_types?.map((t) => AD_TYPE_LABELS[t] || t).join(", "),
                    ]
                      .filter(Boolean)
                      .join(" · ") || "Без ограничений"}
                  </td>
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

          <Field label="Гео (страны СНГ)" hint="Пусто — все страны">
            <div className="flex flex-wrap gap-1.5">
              {COUNTRIES.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setForm({ ...form, geo: toggleArr(form.geo, c) })}
                  className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
                    form.geo.includes(c)
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-border text-muted hover:text-foreground"
                  }`}
                >
                  {c}
                </button>
              ))}
            </div>
          </Field>

          <Field label="Устройства" hint="Пусто — все устройства">
            <div className="flex flex-wrap gap-1.5">
              {DEVICES.map((d) => (
                <button
                  key={d.value}
                  type="button"
                  onClick={() => setForm({ ...form, devices: toggleArr(form.devices, d.value) })}
                  className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
                    form.devices.includes(d.value)
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-border text-muted hover:text-foreground"
                  }`}
                >
                  {d.label}
                </button>
              ))}
            </div>
          </Field>

          <Field label="Форматы" hint="Пусто — все форматы">
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(AD_TYPE_LABELS).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setForm({ ...form, adTypes: toggleArr(form.adTypes, value) })}
                  className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
                    form.adTypes.includes(value)
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-border text-muted hover:text-foreground"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </Field>

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
