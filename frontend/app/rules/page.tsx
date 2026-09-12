"use client"

import { useState } from "react"
import useSWR, { mutate } from "swr"
import { api, fetcher } from "@/lib/api"
import type { Campaign, Rule } from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Field, inputCls, Modal, Spinner, StatusBadge, Table } from "@/components/ui"

const METRICS = [
  { value: "ctr", label: "CTR, %" },
  { value: "win_rate", label: "Win Rate, %" },
  { value: "spend", label: "Расход, ₽" },
  { value: "cpm", label: "eCPM, ₽" },
  { value: "cpc", label: "CPC, ₽" },
  { value: "cpa", label: "CPA (цена конверсии), ₽" },
  { value: "cvr", label: "CVR (конверсия из клика), %" },
  { value: "conversions", label: "Конверсии" },
  { value: "impressions", label: "Показы" },
  { value: "clicks", label: "Клики" },
  { value: "budget_spent_pct", label: "Израсходовано бюджета, %" },
]

const OPERATORS = [
  { value: "lt", label: "меньше" },
  { value: "lte", label: "меньше или равно" },
  { value: "gt", label: "больше" },
  { value: "gte", label: "больше или равно" },
]

const ACTIONS = [
  { value: "pause_campaign", label: "Поставить кампанию на паузу" },
  { value: "pause_line_item", label: "Поставить позиции на паузу" },
  { value: "decrease_bid", label: "Понизить ставку на %" },
  { value: "increase_bid", label: "Повысить ставку на %" },
  { value: "decrease_daily_budget", label: "Понизить дневной бюджет на %" },
  { value: "increase_daily_budget", label: "Повысить дневной бюджет на %" },
  { value: "blacklist_domains", label: "Автоблэклист площадок без кликов (значение = мин. показов)" },
  { value: "notify", label: "Только зафиксировать срабатывание" },
]

interface RuleTemplate {
  key: string
  name: string
  description: string
  metric: string
  operator: string
  threshold: number
  action: string
  action_value: number | null
  window_hours: number
}

export default function RulesPage() {
  const { data: rules } = useSWR<Rule[]>("/rules", fetcher)
  const { data: campaigns } = useSWR<Campaign[]>("/campaigns", fetcher)
  const { data: templates } = useSWR<RuleTemplate[]>("/rules/templates", fetcher)
  const [open, setOpen] = useState(false)
  const [templatesOpen, setTemplatesOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [runResult, setRunResult] = useState<string | null>(null)
  const [form, setForm] = useState({
    name: "",
    campaign_id: "",
    metric: "ctr",
    operator: "lt",
    threshold: "0.1",
    action: "pause_campaign",
    action_value: "10",
    window_hours: "24",
  })

  const openCreate = () => {
    setError(null)
    setForm({ name: "", campaign_id: "", metric: "ctr", operator: "lt", threshold: "0.1", action: "pause_campaign", action_value: "10", window_hours: "24" })
    setOpen(true)
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      await api("/rules", {
        method: "POST",
        body: {
          name: form.name,
          campaign_id: form.campaign_id || null,
          metric: form.metric,
          operator: form.operator,
          threshold: Number(form.threshold),
          action: form.action,
          action_value: form.action_value ? Number(form.action_value) : null,
          window_hours: Number(form.window_hours),
        },
      })
      await mutate("/rules")
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка сохранения")
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (r: Rule) => {
    await api(`/rules/${r.id}`, { method: "PATCH", body: { status: r.status === "active" ? "paused" : "active" } })
    await mutate("/rules")
  }

  const remove = async (r: Rule) => {
    await api(`/rules/${r.id}`, { method: "DELETE" })
    await mutate("/rules")
  }

  const runNow = async () => {
    setRunning(true)
    setRunResult(null)
    try {
      const res = await api<{ evaluated: number; triggered: number }>("/rules/evaluate", { method: "POST" })
      setRunResult(`Проверено правил: ${res.evaluated}, сработало: ${res.triggered}`)
      await mutate("/rules")
      await mutate("/campaigns")
    } catch (err) {
      setRunResult(err instanceof Error ? err.message : "Ошибка запуска")
    } finally {
      setRunning(false)
    }
  }

  const describeRule = (r: Rule) => {
    const m = METRICS.find((x) => x.value === r.metric)?.label || r.metric
    const o = OPERATORS.find((x) => x.value === r.operator)?.label || r.operator
    const a = ACTIONS.find((x) => x.value === r.action)?.label || r.action
    return `Если ${m} ${o} ${r.threshold} за ${r.window_hours} ч → ${a}${r.action_value ? ` (${r.action_value}%)` : ""}`
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Автоправила</h1>
          <p className="mt-0.5 text-sm text-muted">Автоматическая оптимизация кампаний по метрикам</p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={runNow} disabled={running}>
            {running ? "Проверка…" : "Проверить сейчас"}
          </Button>
          <Button variant="secondary" onClick={() => setTemplatesOpen(true)}>
            Из шаблона
          </Button>
          <Button onClick={openCreate}>Создать правило</Button>
        </div>
      </div>

      {runResult ? (
        <div className="rounded-md border border-accent/30 bg-accent/5 px-4 py-3 text-sm text-accent">{runResult}</div>
      ) : null}

      <Card>
        <CardHeader title="Правила оптимизации" subtitle="Правила проверяются автоматически и вручную" />
        {rules ? (
          rules.length > 0 ? (
            <Table headers={["Название", "Условие", "Кампания", "Статус", "Последнее срабатывание", ""]}>
              {rules.map((r) => {
                const camp = campaigns?.find((c) => c.id === r.campaign_id)
                return (
                  <tr key={r.id} className="hover:bg-surface">
                    <td className="px-4 py-3 font-medium text-foreground">{r.name}</td>
                    <td className="px-4 py-3 text-xs text-muted">{describeRule(r)}</td>
                    <td className="px-4 py-3 text-muted">{camp?.name || "Все кампании"}</td>
                    <td className="px-4 py-3">
                      <StatusBadge status={r.status} />
                    </td>
                    <td className="px-4 py-3 text-xs text-muted">
                      {r.last_triggered_at ? new Date(r.last_triggered_at).toLocaleString("ru-RU") : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-2">
                        <Button variant="ghost" onClick={() => toggle(r)}>
                          {r.status === "active" ? "Выключить" : "Включить"}
                        </Button>
                        <Button variant="ghost" onClick={() => remove(r)}>
                          Удалить
                        </Button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </Table>
          ) : (
            <EmptyState
              title="Нет правил"
              description="Создайте правило — например, ставить кампанию на паузу при низком CTR."
              action={<Button onClick={openCreate}>Создать правило</Button>}
            />
          )
        ) : (
          <Spinner />
        )}
      </Card>

      <Modal open={templatesOpen} onClose={() => setTemplatesOpen(false)} title="Готовые правила" wide>
        <div className="space-y-2">
          <p className="text-sm text-muted">
            Выберите шаблон — форма создания откроется с заполненными условиями, их можно скорректировать.
          </p>
          {(templates || []).map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => {
                setForm({
                  name: t.name,
                  campaign_id: "",
                  metric: t.metric,
                  operator: t.operator,
                  threshold: String(t.threshold),
                  action: t.action,
                  action_value: t.action_value != null ? String(t.action_value) : "10",
                  window_hours: String(t.window_hours),
                })
                setError(null)
                setTemplatesOpen(false)
                setOpen(true)
              }}
              className="flex w-full items-start justify-between gap-3 rounded-xl border border-border p-3 text-left transition-colors hover:bg-surface"
            >
              <div>
                <p className="text-sm font-medium text-foreground">{t.name}</p>
                <p className="mt-0.5 text-xs text-muted">{t.description}</p>
              </div>
              <span className="shrink-0 rounded-md bg-panel px-2 py-1 font-mono text-[10px] text-muted">
                {t.window_hours} ч
              </span>
            </button>
          ))}
          {!templates ? <Spinner /> : null}
        </div>
      </Modal>

      <Modal open={open} onClose={() => setOpen(false)} title="Новое правило" wide>
        <form onSubmit={save} className="space-y-4">
          <Field label="Название">
            <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={inputCls} placeholder="Пауза при низком CTR" />
          </Field>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Кампания" hint="Пусто — применяется ко всем">
              <select value={form.campaign_id} onChange={(e) => setForm({ ...form, campaign_id: e.target.value })} className={inputCls}>
                <option value="">Все кампании</option>
                {(campaigns || []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Окно анализа, часов">
              <input type="number" min="1" max="720" value={form.window_hours} onChange={(e) => setForm({ ...form, window_hours: e.target.value })} className={inputCls} />
            </Field>
            <Field label="Метрика">
              <select value={form.metric} onChange={(e) => setForm({ ...form, metric: e.target.value })} className={inputCls}>
                {METRICS.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Условие">
              <div className="flex gap-2">
                <select value={form.operator} onChange={(e) => setForm({ ...form, operator: e.target.value })} className={inputCls}>
                  {OPERATORS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
                <input type="number" step="0.0001" required value={form.threshold} onChange={(e) => setForm({ ...form, threshold: e.target.value })} className={inputCls} />
              </div>
            </Field>
            <Field label="Действие">
              <select value={form.action} onChange={(e) => setForm({ ...form, action: e.target.value })} className={inputCls}>
                {ACTIONS.map((a) => (
                  <option key={a.value} value={a.value}>
                    {a.label}
                  </option>
                ))}
              </select>
            </Field>
            {form.action === "decrease_bid" || form.action === "increase_bid" ? (
              <Field label="Изменение ставки, %">
                <input type="number" min="1" max="90" value={form.action_value} onChange={(e) => setForm({ ...form, action_value: e.target.value })} className={inputCls} />
              </Field>
            ) : null}
          </div>
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
