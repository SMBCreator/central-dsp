"use client"

import { useState } from "react"
import useSWR, { mutate } from "swr"

import { Button } from "@/components/ui"
import { api, describeApiError, fetcher } from "@/lib/api"

/** Открытая ошибка ЕРИР (ord_notifications): объект принят ОРД, но отклонён ЕРИР. */
export interface OrdNotification {
  id: string
  object_type: "organisation" | "contract" | "creative" | "invoice" | "platform" | "unknown"
  external_id?: string | null
  object_external_id?: string | null
  text: string
  url?: string | null
  ord_date?: string | null
  created_at: string
}

export type OrdNotificationTab = "counterparties" | "contracts" | "creatives" | "invoices"

const TYPE_LABELS: Record<OrdNotification["object_type"], string> = {
  organisation: "Контрагент",
  contract: "Договор",
  creative: "Креатив (erid)",
  invoice: "Акт",
  platform: "Площадка",
  unknown: "Объект",
}

const TYPE_TAB: Partial<Record<OrdNotification["object_type"], OrdNotificationTab>> = {
  organisation: "counterparties",
  contract: "contracts",
  creative: "creatives",
  invoice: "invoices",
}

export const ORD_NOTIFICATIONS_KEY = "/ord/notifications"

function shortId(id?: string | null): string {
  if (!id) return "—"
  const [base, version] = id.split(":v")
  return `${base.slice(0, 8)}…${version ? ` v${version}` : ""}`
}

/**
 * Блок «Ошибки ЕРИР» на /ord: список открытых уведомлений и переход на вкладку
 * объекта. Закрываются автоматически успешной повторной регистрацией объекта.
 */
export function OrdNotificationsPanel({
  canWrite,
  onGoTo,
}: {
  canWrite: boolean
  onGoTo: (tab: OrdNotificationTab) => void
}) {
  const { data } = useSWR<{ items: OrdNotification[]; total: number }>(ORD_NOTIFICATIONS_KEY, fetcher)
  const [open, setOpen] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [syncText, setSyncText] = useState<string | null>(null)

  const items = data?.items || []
  if (!data || items.length === 0) return null

  async function syncNow() {
    setSyncing(true)
    setSyncText(null)
    try {
      const res = await api<{ fetched: number; new: number }>("/ord/notifications/sync", { method: "POST" })
      setSyncText(res.new ? `Новых ошибок: ${res.new}` : "Новых ошибок нет")
      await Promise.all([mutate(ORD_NOTIFICATIONS_KEY), mutate("/ord/counterparties"), mutate("/ord/contracts"), mutate("/ord/creatives"), mutate("/ord/invoices")])
    } catch (err) {
      setSyncText(describeApiError(err))
    } finally {
      setSyncing(false)
    }
  }

  return (
    <section aria-labelledby="erir-errors-title" className="rounded-2xl border border-danger/30 bg-danger/5">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="erir-errors-list"
          className="flex items-center gap-2 text-left"
        >
          <span className="inline-flex shrink-0 items-center whitespace-nowrap rounded-md bg-danger px-2 py-0.5 text-xs font-semibold text-background">
            Ошибки ЕРИР: {items.length}
          </span>
          <span id="erir-errors-title" className="text-sm text-foreground">
            ОРД принял объекты, но ЕРИР их отклонил — исправьте данные и зарегистрируйте повторно
          </span>
          <span aria-hidden className="text-xs text-muted">{open ? "▲" : "▼"}</span>
        </button>
        <div className="flex items-center gap-2">
          {syncText ? <span className="text-xs text-muted">{syncText}</span> : null}
          {canWrite ? (
            <Button variant="secondary" disabled={syncing} onClick={syncNow}>
              {syncing ? "Проверка…" : "Проверить сейчас"}
            </Button>
          ) : null}
        </div>
      </div>
      {open ? (
        <ul id="erir-errors-list" className="divide-y divide-danger/20 border-t border-danger/20">
          {items.map((n) => {
            const tab = TYPE_TAB[n.object_type]
            return (
              <li key={n.id} className="flex flex-wrap items-start justify-between gap-3 px-4 py-3 text-sm">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-foreground">{TYPE_LABELS[n.object_type]}</span>
                    <code className="rounded-lg bg-panel px-2 py-0.5 font-mono text-xs text-muted" title={n.external_id || undefined}>
                      {shortId(n.external_id)}
                    </code>
                    <span className="text-xs text-muted">{String(n.ord_date || n.created_at).slice(0, 10)}</span>
                  </div>
                  <p className="mt-1 text-pretty text-danger">{n.text || "Без описания"}</p>
                </div>
                <div className="flex items-center gap-2">
                  {n.url ? (
                    <a href={n.url} target="_blank" rel="noreferrer" className="text-xs text-accent underline-offset-2 hover:underline">
                      В кабинете ОРД
                    </a>
                  ) : null}
                  {tab ? (
                    <Button variant="secondary" onClick={() => onGoTo(tab)}>
                      Перейти
                    </Button>
                  ) : null}
                </div>
              </li>
            )
          })}
        </ul>
      ) : null}
    </section>
  )
}
