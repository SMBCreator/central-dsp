"use client"

import { useState } from "react"
import useSWR, { mutate } from "swr"
import { api, fetcher } from "@/lib/api"
import { Button, Card, CardHeader, EmptyState, Field, inputCls, Modal, Spinner, Table } from "@/components/ui"
import { ConfirmDialog } from "@/components/confirm-dialog"

type Segment = {
  id: string
  name: string
  pixel_id: string
  pixel_name: string
  rule_type: string
  rule_value: string | null
  ttl_days: number
  is_active: boolean
  members: number
  created_at: string
}

type Pixel = { id: string; name: string }

const RULE_TYPES = [
  { value: "all_visitors", label: "Все посетители сайта" },
  { value: "url_contains", label: "URL содержит" },
  { value: "event", label: "Событие (cdsp.track)" },
  { value: "goal", label: "Достигшие цели" },
]

const ruleLabel = (rt: string) => RULE_TYPES.find((r) => r.value === rt)?.label || rt

export default function AudiencesPage() {
  const { data: segments, isLoading } = useSWR<Segment[]>("/audiences", fetcher)
  const { data: pixels } = useSWR<Pixel[]>("/pixels", fetcher)
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ pixel_id: "", name: "", rule_type: "all_visitors", rule_value: "", ttl_days: 30 })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState("")

  const create = async () => {
    setSaving(true)
    setErr("")
    try {
      await api("/audiences", {
        method: "POST",
        body: {
          pixel_id: form.pixel_id || pixels?.[0]?.id,
          name: form.name,
          rule_type: form.rule_type,
          rule_value: form.rule_type === "all_visitors" ? null : form.rule_value || null,
          ttl_days: form.ttl_days,
        },
      })
      await mutate("/audiences")
      setOpen(false)
      setForm({ pixel_id: "", name: "", rule_type: "all_visitors", rule_value: "", ttl_days: 30 })
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Не удалось создать сегмент")
    } finally {
      setSaving(false)
    }
  }

  const toggleActive = async (s: Segment) => {
    await api(`/audiences/${s.id}`, { method: "PATCH", body: { is_active: !s.is_active } })
    await mutate("/audiences")
  }

  // H2: модалка подтверждения вместо confirm()
  const [removing, setRemoving] = useState<Segment | null>(null)
  const [removeBusy, setRemoveBusy] = useState(false)
  const remove = (s: Segment) => setRemoving(s)
  const confirmRemove = async () => {
    if (!removing) return
    setRemoveBusy(true)
    try {
      await api(`/audiences/${removing.id}`, { method: "DELETE" })
      await mutate("/audiences")
      setRemoving(null)
    } finally {
      setRemoveBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <ConfirmDialog
        open={removing !== null}
        title="Удалить сегмент?"
        description={`Сегмент «${removing?.name ?? ""}» будет удалён. Кампании, использующие его в таргетинге, перестанут учитывать этот сегмент.`}
        busy={removeBusy}
        onConfirm={confirmRemove}
        onClose={() => setRemoving(null)}
      />
      <Card>
        <CardHeader
          title="Аудитории"
          subtitle="Сегменты посетителей из Central Pixel для ретаргетинга: включайте или исключайте их в таргетинге кампании"
          action={<Button onClick={() => setOpen(true)}>Создать сегмент</Button>}
        />
        {isLoading ? (
          <Spinner />
        ) : !segments || segments.length === 0 ? (
          <EmptyState
            title="Нет сегментов"
            description="Создайте сегмент на основе пикселя: все посетители, посетители страницы или совершившие событие. Устройства попадают в сегмент после клика по вашей рекламе."
            action={<Button onClick={() => setOpen(true)}>Создать сегмент</Button>}
          />
        ) : (
          <Table headers={["Название", "Пиксель", "Правило", "Устройств", "Время жизни", "Статус", ""]}>
            {segments.map((s) => (
              <tr key={s.id} className="border-t border-border">
                <td className="px-4 py-3 font-medium">{s.name}</td>
                <td className="px-4 py-3 text-muted">{s.pixel_name}</td>
                <td className="px-4 py-3 text-muted">
                  {ruleLabel(s.rule_type)}
                  {s.rule_value ? `: ${s.rule_value}` : ""}
                </td>
                <td className="px-4 py-3 tabular-nums">{s.members}</td>
                <td className="px-4 py-3 text-muted">{s.ttl_days} дн.</td>
                <td className="px-4 py-3">
                  <button
                    type="button"
                    onClick={() => toggleActive(s)}
                    className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                      s.is_active ? "bg-primary/10 text-primary" : "bg-panel text-muted"
                    }`}
                  >
                    {s.is_active ? "Активен" : "Выключен"}
                  </button>
                </td>
                <td className="px-4 py-3 text-right">
                  <Button variant="ghost" onClick={() => remove(s)}>
                    Удалить
                  </Button>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Новый сегмент аудитории">
        <div className="flex flex-col gap-4">
          <Field label="Пиксель" hint="Скрипт должен стоять на сайте рекламодателя">
            <select
              className={inputCls}
              value={form.pixel_id || pixels?.[0]?.id || ""}
              onChange={(e) => setForm({ ...form, pixel_id: e.target.value })}
            >
              {(pixels || []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Название">
            <input
              className={inputCls}
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Посетители за 30 дней"
            />
          </Field>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Правило">
              <select
                className={inputCls}
                value={form.rule_type}
                onChange={(e) => setForm({ ...form, rule_type: e.target.value })}
              >
                {RULE_TYPES.map((r) => (
                  <option key={r.value} value={r.value}>
                    {r.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Время жизни, дней" hint="1–180">
              <input
                type="number"
                min={1}
                max={180}
                className={inputCls}
                value={form.ttl_days}
                onChange={(e) => setForm({ ...form, ttl_days: Number(e.target.value) || 30 })}
              />
            </Field>
          </div>
          {form.rule_type !== "all_visitors" ? (
            <Field
              label={form.rule_type === "url_contains" ? "Подстрока URL" : form.rule_type === "event" ? "Имя события" : "ID цели"}
            >
              <input
                className={inputCls}
                value={form.rule_value}
                onChange={(e) => setForm({ ...form, rule_value: e.target.value })}
                placeholder={form.rule_type === "url_contains" ? "/checkout" : form.rule_type === "event" ? "purchase" : ""}
              />
            </Field>
          ) : null}
          {err ? <p className="text-sm text-destructive">{err}</p> : null}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button onClick={create} disabled={saving || !form.name || !(form.pixel_id || pixels?.[0]?.id)}>
              {saving ? "Создание..." : "Создать"}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
