"use client"

import { useState } from "react"
import useSWR, { mutate } from "swr"
import { api, fetcher } from "@/lib/api"
import { AD_TYPE_LABELS, fmtMoney, type SupplyPartner } from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Field, inputCls, Modal, Spinner, StatusBadge, Table } from "@/components/ui"

export default function SupplyPage() {
  const { data: partners } = useSWR<SupplyPartner[]>("/supply-partners", fetcher)
  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  const [form, setForm] = useState({
    name: "",
    bid_floor: "0.1",
    qps_limit: "100",
    auction_type: "2",
    allowed: ["banner", "native", "video"] as string[],
    notes: "",
  })

  const openCreate = () => {
    setError(null)
    setForm({ name: "", bid_floor: "0.1", qps_limit: "100", auction_type: "2", allowed: ["banner", "native", "video"], notes: "" })
    setOpen(true)
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      await api("/supply-partners", {
        method: "POST",
        body: {
          name: form.name,
          bid_floor: Number(form.bid_floor),
          qps_limit: Number(form.qps_limit),
          auction_type: Number(form.auction_type),
          allowed_ad_types: form.allowed,
          notes: form.notes || null,
        },
      })
      await mutate("/supply-partners")
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка сохранения")
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (p: SupplyPartner) => {
    await api(`/supply-partners/${p.id}`, {
      method: "PATCH",
      body: { status: p.status === "active" ? "paused" : "active" },
    })
    await mutate("/supply-partners")
  }

  const copyEndpoint = (p: SupplyPartner) => {
    const url = `${window.location.origin}/api/rtb/bid/${p.endpoint_key}`
    navigator.clipboard.writeText(url)
    setCopied(p.id)
    setTimeout(() => setCopied(null), 1500)
  }

  const toggleAllowed = (v: string) =>
    setForm({
      ...form,
      allowed: form.allowed.includes(v) ? form.allowed.filter((x) => x !== v) : [...form.allowed, v],
    })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground">SSP-партнёры</h1>
          <p className="mt-0.5 text-sm text-muted">Подключение источников трафика по протоколу OpenRTB 2.5</p>
        </div>
        <Button onClick={openCreate}>Подключить SSP</Button>
      </div>

      <Card>
        <CardHeader
          title="Подключённые SSP"
          subtitle="Каждый партнёр получает уникальный endpoint для отправки BidRequest"
        />
        {partners ? (
          partners.length > 0 ? (
            <Table headers={["Название", "Статус", "Endpoint", "Мин. ставка", "QPS", "Форматы", ""]}>
              {partners.map((p) => (
                <tr key={p.id} className="hover:bg-surface">
                  <td className="px-4 py-3 font-medium text-foreground">{p.name}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={p.status} />
                  </td>
                  <td className="px-4 py-3">
                    <button
                      type="button"
                      onClick={() => copyEndpoint(p)}
                      className="rounded bg-surface px-2 py-1 font-mono text-xs text-accent hover:bg-border/50"
                      title="Скопировать URL"
                    >
                      {copied === p.id ? "Скопировано" : `…/rtb/bid/${p.endpoint_key.slice(0, 10)}…`}
                    </button>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtMoney(p.bid_floor)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{p.qps_limit}</td>
                  <td className="px-4 py-3 text-xs text-muted">
                    {p.allowed_ad_types.map((t) => AD_TYPE_LABELS[t] || t).join(", ")}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end">
                      <Button variant="ghost" onClick={() => toggle(p)}>
                        {p.status === "active" ? "Отключить" : "Включить"}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </Table>
          ) : (
            <EmptyState
              title="Нет подключённых SSP"
              description="Подключите SSP-партнёра, чтобы получать OpenRTB-трафик и участвовать в аукционах."
              action={<Button onClick={openCreate}>Подключить SSP</Button>}
            />
          )
        ) : (
          <Spinner />
        )}
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Подключение SSP">
        <form onSubmit={save} className="space-y-4">
          <Field label="Название партнёра">
            <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={inputCls} placeholder="AdMixer / Buzzoola / MyTarget SSP" />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Мин. ставка (bid floor), USD">
              <input type="number" min="0" step="0.01" value={form.bid_floor} onChange={(e) => setForm({ ...form, bid_floor: e.target.value })} className={inputCls} />
            </Field>
            <Field label="Лимит QPS">
              <input type="number" min="1" value={form.qps_limit} onChange={(e) => setForm({ ...form, qps_limit: e.target.value })} className={inputCls} />
            </Field>
          </div>
          <Field label="Тип аукциона">
            <select value={form.auction_type} onChange={(e) => setForm({ ...form, auction_type: e.target.value })} className={inputCls}>
              <option value="1">Первая цена</option>
              <option value="2">Вторая цена</option>
            </select>
          </Field>
          <Field label="Разрешённые форматы">
            <div className="flex gap-1.5">
              {Object.entries(AD_TYPE_LABELS).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => toggleAllowed(value)}
                  className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
                    form.allowed.includes(value)
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-border text-muted hover:text-foreground"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </Field>
          <Field label="Заметки">
            <input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className={inputCls} placeholder="Контакт менеджера, особенности интеграции" />
          </Field>
          {error ? <p className="text-xs text-danger">{error}</p> : null}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? "Подключение…" : "Подключить"}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  )
}
