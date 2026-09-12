"use client"

import { useEffect, useState } from "react"
import useSWR from "swr"
import { api, describeApiError, fetcher } from "@/lib/api"
import { Button, Card, CardHeader, EmptyState, Field, inputCls, KpiCard, PillTabs, Spinner, Table } from "@/components/ui"
import { NotifyEmailsField } from "@/components/notify-emails-field"
import { toastError, toastSuccess } from "@/components/toaster"
import { emailsError, formatEmails, parseEmails } from "@/lib/notify-emails"

/**
 * Страница суперадмина «Алерты и письма» (audit C4b / N-04).
 *  - куда уходят письма о технических алертах (+ тестовое письмо);
 *  - состояние pipeline Alertmanager -> backend -> SMTP;
 *  - активные алерты и лента переходов firing/resolved;
 *  - последние письма outbox со статусом доставки и ошибкой.
 */

type Severity = "info" | "warning" | "critical"

interface AlertingSettings {
  emails: string[]
  email_min_severity: Severity
  email_on_resolve: boolean
  effective_recipients: string[]
  recipients_source: string
  smtp_configured: boolean
  webhook_secret_configured: boolean
}

interface AlertEvent {
  id: number
  fingerprint: string
  alertname: string
  severity: string
  status: "firing" | "resolved"
  starts_at: string | null
  ends_at: string | null
  summary: string | null
  description: string | null
  labels: Record<string, string> | null
  external_url: string | null
  received_at: string
}

interface ActiveAlert {
  fingerprint: string
  alertname: string
  severity: string
  starts_at: string | null
  summary: string | null
}

interface EventsResponse {
  events: AlertEvent[]
  active: ActiveAlert[]
  pipeline: {
    webhook_secret_configured: boolean
    smtp_configured: boolean
    watchdog_last_received_at: number | null
    watchdog_age_seconds: number | null
    healthy: boolean
  }
}

type OutboxStatus = "queued" | "sending" | "sent" | "failed"

interface OutboxRow {
  id: number
  channel: string
  recipient: string
  kind: string
  subject: string
  status: OutboxStatus
  attempts: number
  next_attempt_at: string | null
  last_error: string | null
  source: string | null
  entity_type: string | null
  entity_id: string | null
  created_at: string
  sent_at: string | null
}

interface OutboxResponse {
  items: OutboxRow[]
  counts: Partial<Record<OutboxStatus, number>>
}

const SEVERITY_LABELS: Record<string, string> = { info: "Инфо", warning: "Предупреждение", critical: "Критично" }
const SEVERITY_OPTIONS: { key: Severity; label: string }[] = [
  { key: "info", label: "Инфо и выше (все)" },
  { key: "warning", label: "Предупреждение и выше" },
  { key: "critical", label: "Только критичные" },
]
const SOURCE_LABELS: Record<string, string> = {
  settings: "адреса из настроек",
  superadmins: "email всех активных суперадминов",
  none: "адресов нет",
}
const OUTBOX_STATUS_LABELS: Record<OutboxStatus, string> = {
  queued: "В очереди",
  sending: "Отправляется",
  sent: "Отправлено",
  failed: "Не доставлено",
}
const EVENT_FILTERS = [
  { key: "all", label: "Все" },
  { key: "firing", label: "Сработали" },
  { key: "resolved", label: "Разрешены" },
] as const
const OUTBOX_FILTERS = [
  { key: "all", label: "Все" },
  { key: "queued", label: "В очереди" },
  { key: "sent", label: "Отправлены" },
  { key: "failed", label: "Не доставлены" },
] as const

function fmtTs(iso: string | null | undefined): string {
  if (!iso) return "—"
  return new Date(iso).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "medium" })
}

function fmtAge(seconds: number | null): string {
  if (seconds === null) return "не приходил"
  if (seconds < 90) return `${Math.round(seconds)} с назад`
  if (seconds < 5400) return `${Math.round(seconds / 60)} мин назад`
  return `${(seconds / 3600).toFixed(1)} ч назад`
}

function severityTone(severity: string): string {
  if (severity === "critical") return "bg-danger/10 text-danger"
  if (severity === "warning") return "bg-warning/10 text-warning"
  return "bg-accent/10 text-accent"
}

function outboxTone(status: OutboxStatus): string {
  if (status === "sent") return "bg-success/10 text-success"
  if (status === "failed") return "bg-danger/10 text-danger"
  return "bg-warning/10 text-warning"
}

function Pill({ tone, children }: { tone: string; children: React.ReactNode }) {
  return <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${tone}`}>{children}</span>
}

export default function AdminAlertsPage() {
  const [eventFilter, setEventFilter] = useState<(typeof EVENT_FILTERS)[number]["key"]>("all")
  const [outboxFilter, setOutboxFilter] = useState<(typeof OUTBOX_FILTERS)[number]["key"]>("all")

  const settingsSwr = useSWR<AlertingSettings>("/settings/alerting", fetcher)
  const eventsSwr = useSWR<EventsResponse>(
    `/alerts/events?limit=100${eventFilter === "all" ? "" : `&status=${eventFilter}`}`,
    fetcher,
    { refreshInterval: 30_000 },
  )
  const outboxSwr = useSWR<OutboxResponse>(
    `/alerts/outbox?limit=50${outboxFilter === "all" ? "" : `&status=${outboxFilter}`}`,
    fetcher,
    { refreshInterval: 30_000 },
  )

  const firstError = settingsSwr.error || eventsSwr.error || outboxSwr.error
  if (firstError) {
    return (
      <Card>
        <EmptyState
          title="Не удалось загрузить данные"
          description={describeApiError(firstError)}
          action={
            <Button variant="secondary" onClick={() => { settingsSwr.mutate(); eventsSwr.mutate(); outboxSwr.mutate() }}>
              Повторить
            </Button>
          }
        />
      </Card>
    )
  }
  if (!settingsSwr.data || !eventsSwr.data || !outboxSwr.data) return <Spinner />

  const { pipeline, active, events } = eventsSwr.data
  const counts = outboxSwr.data.counts

  return (
    <div className="flex flex-col gap-6">
      <section aria-label="Состояние доставки" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          label="Alertmanager → backend"
          value={pipeline.healthy ? "Работает" : pipeline.watchdog_last_received_at ? "Нет сигнала" : "Не подключён"}
          hint={`Watchdog: ${fmtAge(pipeline.watchdog_age_seconds)}${pipeline.webhook_secret_configured ? "" : " · секрет webhook не задан"}`}
        />
        <KpiCard
          label="SMTP"
          value={pipeline.smtp_configured ? "Настроен" : "Не настроен"}
          hint={pipeline.smtp_configured ? "Письма уходят раз в минуту" : "Письма копятся в очереди — задайте SMTP_* в окружении"}
        />
        <KpiCard label="Активных алертов" value={String(active.length)} hint={active.length ? "Последний переход — firing" : "Всё спокойно"} />
        <KpiCard
          label="Очередь писем"
          value={String(counts.queued ?? 0)}
          hint={`отправлено ${counts.sent ?? 0} · не доставлено ${counts.failed ?? 0}`}
        />
      </section>

      <AlertingSettingsCard settings={settingsSwr.data} onSaved={(s) => settingsSwr.mutate(s, false)} onTestSent={() => outboxSwr.mutate()} />

      <Card>
        <CardHeader title="Активные алерты" subtitle="Алерты, у которых последний переход — firing" />
        {active.length === 0 ? (
          <EmptyState title="Активных алертов нет" />
        ) : (
          <Table headers={["Алерт", "Важность", "С", "Описание"]}>
            {active.map((a) => (
              <tr key={a.fingerprint} className="border-b border-border/60 last:border-0">
                <td className="whitespace-nowrap px-4 py-2 font-mono text-xs">{a.alertname}</td>
                <td className="px-4 py-2"><Pill tone={severityTone(a.severity)}>{SEVERITY_LABELS[a.severity] || a.severity}</Pill></td>
                <td className="whitespace-nowrap px-4 py-2 text-xs text-muted">{fmtTs(a.starts_at)}</td>
                <td className="px-4 py-2 text-xs">{a.summary || "—"}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Лента переходов"
          subtitle="Каждое сообщение Alertmanager: firing или resolved"
          action={<PillTabs value={eventFilter} onChange={setEventFilter} options={EVENT_FILTERS} />}
        />
        {events.length === 0 ? (
          <EmptyState title="Переходов пока нет" description="Появятся, как только Alertmanager отправит первый webhook" />
        ) : (
          <Table headers={["Получено", "Алерт", "Важность", "Статус", "Описание"]}>
            {events.map((e) => (
              <tr key={e.id} className="border-b border-border/60 last:border-0">
                <td className="whitespace-nowrap px-4 py-2 text-xs text-muted">{fmtTs(e.received_at)}</td>
                <td className="whitespace-nowrap px-4 py-2 font-mono text-xs">
                  {e.external_url ? (
                    <a href={e.external_url} target="_blank" rel="noreferrer" className="underline-offset-2 hover:underline">{e.alertname}</a>
                  ) : e.alertname}
                </td>
                <td className="px-4 py-2"><Pill tone={severityTone(e.severity)}>{SEVERITY_LABELS[e.severity] || e.severity}</Pill></td>
                <td className="px-4 py-2">
                  <Pill tone={e.status === "firing" ? "bg-danger/10 text-danger" : "bg-success/10 text-success"}>
                    {e.status === "firing" ? "Сработал" : "Разрешён"}
                  </Pill>
                </td>
                <td className="max-w-md px-4 py-2 text-xs">{e.summary || e.description || "—"}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Исходящие письма"
          subtitle="Последние 50 писем из очереди: технические алерты, бизнес-уведомления, тестовые"
          action={<PillTabs value={outboxFilter} onChange={setOutboxFilter} options={OUTBOX_FILTERS} />}
        />
        {outboxSwr.data.items.length === 0 ? (
          <EmptyState title="Писем нет" />
        ) : (
          <Table headers={["Создано", "Кому", "Вид", "Тема", "Статус", "Попыток", "Ошибка / отправлено"]}>
            {outboxSwr.data.items.map((m) => (
              <tr key={m.id} className="border-b border-border/60 last:border-0">
                <td className="whitespace-nowrap px-4 py-2 text-xs text-muted">{fmtTs(m.created_at)}</td>
                <td className="whitespace-nowrap px-4 py-2 font-mono text-xs">{m.recipient}</td>
                <td className="whitespace-nowrap px-4 py-2 font-mono text-xs text-muted">{m.kind}</td>
                <td className="max-w-xs truncate px-4 py-2 text-xs" title={m.subject}>{m.subject}</td>
                <td className="px-4 py-2"><Pill tone={outboxTone(m.status)}>{OUTBOX_STATUS_LABELS[m.status]}</Pill></td>
                <td className="px-4 py-2 text-xs tabular-nums">{m.attempts}</td>
                <td className="max-w-xs px-4 py-2 text-xs">
                  {m.status === "sent" ? (
                    <span className="text-muted">{fmtTs(m.sent_at)}</span>
                  ) : m.last_error ? (
                    <span className="break-words text-danger" title={m.last_error}>{m.last_error}</span>
                  ) : m.next_attempt_at ? (
                    <span className="text-muted">следующая попытка {fmtTs(m.next_attempt_at)}</span>
                  ) : "—"}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  )
}

function AlertingSettingsCard({
  settings,
  onSaved,
  onTestSent,
}: {
  settings: AlertingSettings
  onSaved: (s: AlertingSettings) => void
  onTestSent: () => void
}) {
  const [emailsText, setEmailsText] = useState(formatEmails(settings.emails))
  const [minSeverity, setMinSeverity] = useState<Severity>(settings.email_min_severity)
  const [onResolve, setOnResolve] = useState(settings.email_on_resolve)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setEmailsText(formatEmails(settings.emails))
    setMinSeverity(settings.email_min_severity)
    setOnResolve(settings.email_on_resolve)
  }, [settings.emails, settings.email_min_severity, settings.email_on_resolve])

  const save = async () => {
    const parsed = parseEmails(emailsText)
    const problem = emailsError(parsed)
    if (problem) {
      setError(problem)
      return
    }
    setSaving(true)
    setError(null)
    try {
      const next = await api<AlertingSettings>("/settings/alerting", {
        method: "PUT",
        body: { emails: parsed.emails, email_min_severity: minSeverity, email_on_resolve: onResolve },
      })
      onSaved(next)
      toastSuccess("Настройки алертов сохранены")
    } catch (err) {
      setError(describeApiError(err, "Не удалось сохранить"))
    } finally {
      setSaving(false)
    }
  }

  const sendTest = async () => {
    setTesting(true)
    try {
      const res = await api<{ queued: number; recipients: string[]; smtp_configured: boolean }>("/settings/alerting/test", { method: "POST" })
      toastSuccess(
        res.smtp_configured
          ? `Тестовое письмо поставлено в очередь: ${res.recipients.join(", ")}`
          : `Письмо в очереди (${res.recipients.join(", ")}), но SMTP не настроен — оно не уйдёт`,
      )
      onTestSent()
    } catch (err) {
      toastError(describeApiError(err, "Не удалось отправить тестовое письмо"))
    } finally {
      setTesting(false)
    }
  }

  const recipientsLine = settings.effective_recipients.length
    ? `Сейчас письма получают: ${settings.effective_recipients.join(", ")} (${SOURCE_LABELS[settings.recipients_source] || settings.recipients_source}).`
    : "Сейчас письма никто не получает: добавьте адрес здесь или укажите email у суперадмина."

  return (
    <Card>
      <CardHeader
        title="Куда слать технические алерты"
        subtitle="Все правила Prometheus без исключения приходят в кабинет суперадминам; письма — на эти адреса"
        action={
          <Button variant="secondary" onClick={sendTest} disabled={testing}>
            {testing ? "Отправка…" : "Тестовое письмо"}
          </Button>
        }
      />
      <div className="flex flex-col gap-4 px-5 pb-5">
        <p className={"text-xs " + (settings.effective_recipients.length ? "text-muted" : "text-danger")} role={settings.effective_recipients.length ? undefined : "alert"}>
          {recipientsLine}
        </p>
        <NotifyEmailsField
          label="Email для технических алертов"
          value={emailsText}
          onChange={setEmailsText}
          fallbackHint="Если список пуст — письма уходят на email всех активных суперадминов."
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Слать письма начиная с важности" hint="В кабинет приходят все алерты; порог касается только писем.">
            <select value={minSeverity} onChange={(e) => setMinSeverity(e.target.value as Severity)} className={inputCls}>
              {SEVERITY_OPTIONS.map((o) => (
                <option key={o.key} value={o.key}>{o.label}</option>
              ))}
            </select>
          </Field>
          <label className="flex cursor-pointer items-start gap-2.5 pt-6">
            <input
              type="checkbox"
              checked={onResolve}
              onChange={(e) => setOnResolve(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-border accent-current"
            />
            <span className="text-sm text-foreground">Писать и о разрешении алерта (resolved)</span>
          </label>
        </div>
        {error ? <p className="text-xs text-danger" role="alert">{error}</p> : null}
        <div>
          <Button onClick={save} disabled={saving}>{saving ? "Сохранение…" : "Сохранить"}</Button>
        </div>
      </div>
    </Card>
  )
}
