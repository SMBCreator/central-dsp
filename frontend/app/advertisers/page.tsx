"use client"

import { useState } from "react"
import Link from "next/link"
import { api } from "@/lib/api"
import { refreshAdvertisers, useAdvertisers } from "@/lib/advertisers"
import { fmtMoney, type Advertiser } from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Field, inputCls, Modal, Spinner, StatusBadge, Table } from "@/components/ui"
import { NotifyEmailsField } from "@/components/notify-emails-field"
import { emailsError, formatEmails, parseEmails } from "@/lib/notify-emails"

export default function AdvertisersPage() {
  const { data: advertisers } = useAdvertisers()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Advertiser | null>(null)
  const [form, setForm] = useState({ name: "", domain: "", category: "", notify_emails: "" })
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const openCreate = () => {
    setEditing(null)
    setForm({ name: "", domain: "", category: "", notify_emails: "" })
    setError(null)
    setOpen(true)
  }

  const openEdit = (a: Advertiser) => {
    setEditing(a)
    setForm({ name: a.name, domain: a.domain || "", category: a.category || "", notify_emails: formatEmails(a.notify_emails) })
    setError(null)
    setOpen(true)
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    const parsedEmails = parseEmails(form.notify_emails)
    const emailsProblem = emailsError(parsedEmails)
    if (emailsProblem) {
      setError(emailsProblem)
      return
    }
    setSaving(true)
    setError(null)
    try {
      const body = {
        name: form.name,
        domain: form.domain || null,
        category: form.category || null,
        notify_emails: parsedEmails.emails,
      }
      if (editing) {
        await api(`/advertisers/${editing.id}`, { method: "PATCH", body })
      } else {
        await api("/advertisers", { method: "POST", body })
      }
      await refreshAdvertisers()
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка сохранения")
    } finally {
      setSaving(false)
    }
  }

  const toggleStatus = async (a: Advertiser) => {
    await api(`/advertisers/${a.id}`, {
      method: "PATCH",
      body: { status: a.status === "active" ? "paused" : "active" },
    })
    await refreshAdvertisers()
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Рекламодатели</h1>
          <p className="mt-0.5 text-sm text-muted">Управление аккаунтами рекламодателей и балансами</p>
        </div>
        <Button onClick={openCreate}>Добавить рекламодателя</Button>
      </div>

      <Card>
        <CardHeader title="Список рекламодателей" subtitle={advertisers ? `Всего: ${advertisers.length}` : undefined} />
        {advertisers ? (
          advertisers.length > 0 ? (
            <Table headers={["ID", "Название", "Домен", "Категория", "Баланс", "Статус", ""]}>
              {advertisers.map((a) => (
                <tr key={a.id} className="hover:bg-surface">
                  <td className="px-4 py-3 font-mono text-xs text-muted">{a.display_id}</td>
                  <td className="px-4 py-3 font-medium text-foreground">{a.name}</td>
                  <td className="px-4 py-3 text-muted">{a.domain || "—"}</td>
                  <td className="px-4 py-3 text-muted">{a.category || "—"}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtMoney(a.balance, a.currency)}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={a.status} />
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-2">
                      <Button variant="ghost" onClick={() => toggleStatus(a)}>
                        {a.status === "active" ? "Пауза" : "Активировать"}
                      </Button>
                      <Button variant="ghost" onClick={() => openEdit(a)}>
                        Изменить
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </Table>
          ) : (
            <EmptyState
              title="Нет рекламодателей"
              description="Добавьте первого рекламодателя, чтобы создавать кампании."
              action={<Button onClick={openCreate}>Добавить</Button>}
            />
          )
        ) : (
          <Spinner />
        )}
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title={editing ? "Изменить рекламодателя" : "Новый рекламодатель"}>
        <form onSubmit={save} className="space-y-4">
          <Field label="Название">
            <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={inputCls} placeholder="ООО Ромашка" />
          </Field>
          <Field label="Домен">
            <input value={form.domain} onChange={(e) => setForm({ ...form, domain: e.target.value })} className={inputCls} placeholder="romashka.ru" />
          </Field>
          <Field label="Категория">
            <input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} className={inputCls} placeholder="E-commerce" />
          </Field>
          <NotifyEmailsField
            value={form.notify_emails}
            onChange={(v) => setForm({ ...form, notify_emails: v })}
            fallbackHint="Если список пуст — письма получает владелец аккаунта."
          />
          <p className="text-xs text-muted">
            Баланс нового рекламодателя равен 0. Пополнения и корректировки выполняются в разделе{" "}
            <Link href="/billing" className="underline">
              Биллинг
            </Link>{" "}
            и попадают в журнал операций.
          </p>
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
