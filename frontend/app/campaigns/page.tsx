"use client"

import { useState } from "react"
import Link from "next/link"
import useSWR, { mutate } from "swr"
import { api, fetcher } from "@/lib/api"
import { fmtMoney, type Advertiser, type Campaign } from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Field, inputCls, Modal, Spinner, StatusBadge, Table } from "@/components/ui"

const OBJECTIVES = [
  { value: "awareness", label: "Охват" },
  { value: "traffic", label: "Трафик" },
  { value: "conversions", label: "Конверсии" },
]

export default function CampaignsPage() {
  const { data: campaigns } = useSWR<Campaign[]>("/campaigns", fetcher)
  const { data: advertisers } = useSWR<Advertiser[]>("/advertisers", fetcher)
  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({
    advertiser_id: "",
    name: "",
    objective: "awareness",
    daily_budget: "50",
    total_budget: "1000",
    start_date: "",
    end_date: "",
    pacing: "even",
    frequency_cap_impressions: "",
    frequency_cap_period_hours: "24",
  })

  const openCreate = () => {
    setError(null)
    setForm({
      advertiser_id: advertisers?.[0]?.id || "",
      name: "",
      objective: "awareness",
      daily_budget: "50",
      total_budget: "1000",
      start_date: "",
      end_date: "",
      pacing: "even",
      frequency_cap_impressions: "",
      frequency_cap_period_hours: "24",
    })
    setOpen(true)
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      await api("/campaigns", {
        method: "POST",
        body: {
          advertiser_id: form.advertiser_id,
          name: form.name,
          objective: form.objective,
          daily_budget: Number(form.daily_budget),
          total_budget: Number(form.total_budget),
          start_date: form.start_date || null,
          end_date: form.end_date || null,
          pacing: form.pacing,
          frequency_cap_impressions: form.frequency_cap_impressions ? Number(form.frequency_cap_impressions) : null,
          frequency_cap_period_hours: form.frequency_cap_period_hours ? Number(form.frequency_cap_period_hours) : null,
        },
      })
      await mutate("/campaigns")
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка сохранения")
    } finally {
      setSaving(false)
    }
  }

  const toggleStatus = async (c: Campaign) => {
    await api(`/campaigns/${c.id}`, {
      method: "PATCH",
      body: { status: c.status === "active" ? "paused" : "active" },
    })
    await mutate("/campaigns")
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Кампании</h1>
          <p className="mt-0.5 text-sm text-muted">Управление рекламными кампаниями и бюджетами</p>
        </div>
        <div className="flex items-center gap-2">
          <Link href="/campaigns/new">
            <Button disabled={!advertisers || advertisers.length === 0}>Мастер кампании</Button>
          </Link>
          <Button variant="ghost" onClick={openCreate} disabled={!advertisers || advertisers.length === 0}>
            Быстрое создание
          </Button>
        </div>
      </div>

      {advertisers && advertisers.length === 0 ? (
        <Card>
          <EmptyState
            title="Сначала добавьте рекламодателя"
            description="Кампания создаётся для конкретного рекламодателя."
            action={
              <Link href="/advertisers">
                <Button>К рекламодателям</Button>
              </Link>
            }
          />
        </Card>
      ) : null}

      <Card>
        <CardHeader title="Все кампании" subtitle={campaigns ? `Всего: ${campaigns.length}` : undefined} />
        {campaigns ? (
          campaigns.length > 0 ? (
            <Table headers={["Название", "Цель", "Статус", "Дневной бюджет", "Расход сегодня", "Расход всего", ""]}>
              {campaigns.map((c) => (
                <tr key={c.id} className="hover:bg-surface">
                  <td className="px-4 py-3">
                    <Link href={`/campaigns/${c.id}`} className="font-medium text-foreground hover:text-accent">
                      {c.name}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-muted">{OBJECTIVES.find((o) => o.value === c.objective)?.label || c.objective}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={c.status} />
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtMoney(c.daily_budget)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtMoney(c.spent_today)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtMoney(c.spent_total)}</td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end">
                      <Button variant="ghost" onClick={() => toggleStatus(c)}>
                        {c.status === "active" ? "Пауза" : "Запустить"}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </Table>
          ) : (
            <EmptyState title="Нет кампаний" description="Создайте первую кампанию для запуска рекламы." />
          )
        ) : (
          <Spinner />
        )}
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Новая кампания" wide>
        <form onSubmit={save} className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Рекламодатель">
              <select
                required
                value={form.advertiser_id}
                onChange={(e) => setForm({ ...form, advertiser_id: e.target.value })}
                className={inputCls}
              >
                <option value="" disabled>
                  Выберите…
                </option>
                {(advertisers || []).map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Название">
              <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={inputCls} placeholder="Осенняя распродажа" />
            </Field>
            <Field label="Цель">
              <select value={form.objective} onChange={(e) => setForm({ ...form, objective: e.target.value })} className={inputCls}>
                {OBJECTIVES.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Пейсинг">
              <select value={form.pacing} onChange={(e) => setForm({ ...form, pacing: e.target.value })} className={inputCls}>
                <option value="even">Равномерный</option>
                <option value="asap">Как можно быстрее</option>
              </select>
            </Field>
            <Field label="Дневной бюджет, USD">
              <input type="number" required min="0.01" step="0.01" value={form.daily_budget} onChange={(e) => setForm({ ...form, daily_budget: e.target.value })} className={inputCls} />
            </Field>
            <Field label="Общий бюджет, USD">
              <input type="number" required min="0.01" step="0.01" value={form.total_budget} onChange={(e) => setForm({ ...form, total_budget: e.target.value })} className={inputCls} />
            </Field>
            <Field label="Дата начала">
              <input type="date" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} className={inputCls} />
            </Field>
            <Field label="Дата окончания">
              <input type="date" value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} className={inputCls} />
            </Field>
            <Field label="Частотный лимит, показов" hint="Пусто — без ограничений">
              <input type="number" min="1" value={form.frequency_cap_impressions} onChange={(e) => setForm({ ...form, frequency_cap_impressions: e.target.value })} className={inputCls} />
            </Field>
            <Field label="Период лимита, часов">
              <input type="number" min="1" value={form.frequency_cap_period_hours} onChange={(e) => setForm({ ...form, frequency_cap_period_hours: e.target.value })} className={inputCls} />
            </Field>
          </div>
          {error ? <p className="text-xs text-danger">{error}</p> : null}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? "Создание…" : "Создать кампанию"}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  )
}
