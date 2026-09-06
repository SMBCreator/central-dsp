"use client"

import { useState } from "react"
import useSWR, { mutate } from "swr"
import { api, fetcher } from "@/lib/api"
import { fmtMoney } from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Field, inputCls, KpiCard, Modal, Spinner, StatusBadge, Table } from "@/components/ui"

interface BillingSummary {
  advertisers: { id: string; name: string; status: string; balance: number; currency: string }[]
  total_deposited: number
  total_spent: number
  total_adjusted: number
}

interface Tx {
  id: number
  advertiser_id: string | null
  advertiser_name: string | null
  type: string
  amount: number
  balance_after: number | null
  description: string | null
  created_at: string
}

const TX_LABELS: Record<string, string> = {
  deposit: "Пополнение",
  spend: "Расход",
  adjustment: "Корректировка",
  refund: "Возврат",
}

export default function BillingPage() {
  const { data: summary } = useSWR<BillingSummary>("/billing/summary", fetcher)
  const { data: txs } = useSWR<Tx[]>("/billing/transactions?limit=100", fetcher)

  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState({ advertiser_id: "", amount: "100", description: "" })
  // One key per opened form: a retried/double-clicked submit is replayed by the
  // backend instead of crediting the balance twice.
  const [idempotencyKey, setIdempotencyKey] = useState("")

  const openDeposit = (advertiserId?: string) => {
    setError(null)
    setForm({ advertiser_id: advertiserId || summary?.advertisers[0]?.id || "", amount: "100", description: "" })
    setIdempotencyKey(crypto.randomUUID())
    setOpen(true)
  }

  const deposit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      await api("/billing/deposit", {
        method: "POST",
        body: {
          advertiser_id: form.advertiser_id,
          amount: Number(form.amount),
          description: form.description || undefined,
          idempotency_key: idempotencyKey,
        },
      })
      await Promise.all([mutate("/billing/summary"), mutate("/billing/transactions?limit=100")])
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка пополнения")
    } finally {
      setSaving(false)
    }
  }

  if (!summary) return <Spinner />

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Биллинг</h1>
          <p className="mt-0.5 text-sm text-muted">Балансы рекламодателей и история операций</p>
        </div>
        <Button onClick={() => openDeposit()}>Пополнить баланс</Button>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <KpiCard label="Всего пополнено" value={fmtMoney(summary.total_deposited)} />
        <KpiCard label="Всего потрачено" value={fmtMoney(summary.total_spent)} />
        <KpiCard
          label="Текущий остаток"
          value={fmtMoney(summary.advertisers.reduce((s, a) => s + a.balance, 0))}
        />
      </div>

      <Card>
        <CardHeader title="Балансы рекламодателей" subtitle="Кампании автоматически ставятся на паузу при нулевом балансе" />
        {summary.advertisers.length > 0 ? (
          <Table headers={["Рекламодатель", "Статус", "Баланс", ""]}>
            {summary.advertisers.map((a) => (
              <tr key={a.id} className="hover:bg-surface">
                <td className="px-4 py-3 font-medium text-foreground">{a.name}</td>
                <td className="px-4 py-3">
                  <StatusBadge status={a.status} />
                </td>
                <td className={`px-4 py-3 font-mono text-xs ${a.balance <= 0 ? "text-danger" : ""}`}>
                  {fmtMoney(a.balance)} {a.currency}
                </td>
                <td className="px-4 py-3">
                  <div className="flex justify-end">
                    <Button variant="ghost" onClick={() => openDeposit(a.id)}>
                      Пополнить
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </Table>
        ) : (
          <EmptyState title="Нет рекламодателей" description="Создайте рекламодателя, чтобы управлять балансом." />
        )}
      </Card>

      <Card>
        <CardHeader title="История операций" subtitle="Последние 100 транзакций" />
        {txs ? (
          txs.length > 0 ? (
            <Table headers={["Дата", "Рекламодатель", "Тип", "Сумма", "Остаток", "Комментарий"]}>
              {txs.map((t) => (
                <tr key={t.id} className="hover:bg-surface">
                  <td className="px-4 py-3 text-xs text-muted">
                    {new Date(t.created_at).toLocaleString("ru-RU")}
                  </td>
                  <td className="px-4 py-3 text-xs">{t.advertiser_name || "—"}</td>
                  <td className="px-4 py-3 text-xs">{TX_LABELS[t.type] || t.type}</td>
                  <td className={`px-4 py-3 font-mono text-xs ${t.amount < 0 ? "text-danger" : "text-success"}`}>
                    {t.amount > 0 ? "+" : ""}
                    {fmtMoney(t.amount)}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">
                    {t.balance_after !== null ? fmtMoney(t.balance_after) : "—"}
                  </td>
                  <td className="max-w-[240px] truncate px-4 py-3 text-xs text-muted">{t.description || "—"}</td>
                </tr>
              ))}
            </Table>
          ) : (
            <EmptyState title="Операций пока нет" description="Пополните баланс рекламодателя, чтобы начать." />
          )
        ) : (
          <Spinner />
        )}
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Пополнение баланса">
        <form onSubmit={deposit} className="space-y-4">
          <Field label="Рекламодатель">
            <select
              required
              value={form.advertiser_id}
              onChange={(e) => setForm({ ...form, advertiser_id: e.target.value })}
              className={inputCls}
            >
              <option value="" disabled>
                Выберите рекламодателя
              </option>
              {summary.advertisers.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ({fmtMoney(a.balance)})
                </option>
              ))}
            </select>
          </Field>
          <Field label="Сумма, ₽">
            <input
              type="number"
              required
              min="1"
              step="0.01"
              value={form.amount}
              onChange={(e) => setForm({ ...form, amount: e.target.value })}
              className={inputCls}
            />
          </Field>
          <Field label="Комментарий" hint="Необязательно">
            <input
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              className={inputCls}
              placeholder="Счёт №123 от 01.02.2026"
            />
          </Field>
          {error ? <p className="text-xs text-danger">{error}</p> : null}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? "Пополнение…" : "Пополнить"}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  )
}
