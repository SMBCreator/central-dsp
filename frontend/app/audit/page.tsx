"use client"

import { useMemo, useState } from "react"
import useSWR from "swr"
import { downloadCsv, fetcher } from "@/lib/api"
import { Button, Card, CardHeader, EmptyState, Field, Spinner, inputCls } from "@/components/ui"

interface AuditRow {
  id: number
  user_id: string | null
  user_email: string | null
  user_role: string | null
  method: string
  path: string
  status_code: number
  entity: string | null
  entity_id: string | null
  ip: string | null
  created_at: string | null
}

interface AuditPage {
  items: AuditRow[]
  next_before_id: number | null
}

interface EntityOption {
  key: string
  label: string
}

const METHOD_LABELS: Record<string, string> = {
  POST: "Создание",
  PATCH: "Изменение",
  PUT: "Замена",
  DELETE: "Удаление",
}

const PAGE_SIZE = 50

function methodTone(method: string): string {
  if (method === "DELETE") return "bg-danger/10 text-danger"
  if (method === "POST") return "bg-success/10 text-success"
  return "bg-accent/10 text-accent"
}

function statusTone(code: number): string {
  if (code >= 500) return "bg-danger/10 text-danger"
  if (code >= 400) return "bg-warning/10 text-warning"
  return "bg-muted/10 text-muted"
}

function fmtTs(iso: string | null): string {
  if (!iso) return "—"
  const d = new Date(iso)
  return d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "medium" })
}

/** Начало дня в локальной зоне -> ISO для date_from/date_to. */
function dayStartIso(day: string): string | null {
  if (!day) return null
  const d = new Date(`${day}T00:00:00`)
  return Number.isNaN(d.getTime()) ? null : d.toISOString()
}

function nextDayIso(day: string): string | null {
  if (!day) return null
  const d = new Date(`${day}T00:00:00`)
  if (Number.isNaN(d.getTime())) return null
  d.setDate(d.getDate() + 1)
  return d.toISOString()
}

interface Filters {
  entity: string
  entity_id: string
  user_email: string
  method: string
  onlyErrors: boolean
  from: string
  to: string
}

const EMPTY_FILTERS: Filters = { entity: "", entity_id: "", user_email: "", method: "", onlyErrors: false, from: "", to: "" }

function buildQuery(f: Filters, beforeId: number | null): string {
  const q = new URLSearchParams()
  if (f.entity) q.set("entity", f.entity)
  if (f.entity_id.trim()) q.set("entity_id", f.entity_id.trim())
  if (f.user_email.trim()) q.set("user_email", f.user_email.trim())
  if (f.method) q.set("method", f.method)
  if (f.onlyErrors) q.set("status_min", "400")
  const from = dayStartIso(f.from)
  const to = nextDayIso(f.to)
  if (from) q.set("date_from", from)
  if (to) q.set("date_to", to)
  if (beforeId) q.set("before_id", String(beforeId))
  q.set("limit", String(PAGE_SIZE))
  return q.toString()
}

export default function AuditPage() {
  const [draft, setDraft] = useState<Filters>(EMPTY_FILTERS)
  const [applied, setApplied] = useState<Filters>(EMPTY_FILTERS)
  // Стек курсоров: cursors[i] — before_id для страницы i (первая — null).
  const [cursors, setCursors] = useState<Array<number | null>>([null])
  const pageIdx = cursors.length - 1
  const beforeId = cursors[pageIdx]

  const { data: entities } = useSWR<EntityOption[]>("/audit/entities", fetcher)
  const query = useMemo(() => buildQuery(applied, beforeId), [applied, beforeId])
  const { data, isLoading, error } = useSWR<AuditPage>(`/audit/log?${query}`, fetcher, { keepPreviousData: true })

  const entityLabel = useMemo(() => {
    const m = new Map<string, string>()
    for (const e of entities ?? []) m.set(e.key, e.label)
    return (key: string | null) => (key ? m.get(key) ?? key : "—")
  }, [entities])

  const apply = () => {
    setApplied(draft)
    setCursors([null])
  }

  const reset = () => {
    setDraft(EMPTY_FILTERS)
    setApplied(EMPTY_FILTERS)
    setCursors([null])
  }

  const nextPage = () => {
    if (data?.next_before_id) setCursors((c) => [...c, data.next_before_id])
  }

  const prevPage = () => {
    setCursors((c) => (c.length > 1 ? c.slice(0, -1) : c))
  }

  const exportCsv = () => {
    const q = buildQuery(applied, null)
    void downloadCsv(`/audit/log.csv?${q}`, "audit_log.csv")
  }

  const rows = data?.items ?? []
  const hasActiveFilters = applied !== EMPTY_FILTERS && JSON.stringify(applied) !== JSON.stringify(EMPTY_FILTERS)

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <h1 className="text-xl font-semibold">Журнал действий</h1>
          <p className="mt-1 text-sm text-muted text-pretty">
            Все изменения в кабинете: кто, что и когда. Запись ведётся автоматически и не редактируется.
          </p>
        </div>
        <Button variant="secondary" onClick={exportCsv} disabled={rows.length === 0}>
          Скачать CSV
        </Button>
      </div>

      <Card>
        <CardHeader title="Фильтры" subtitle="Период — по локальному времени браузера; в CSV время в UTC" />
        <form
          className="grid grid-cols-1 gap-4 px-4 pb-4 sm:grid-cols-2 lg:grid-cols-4"
          onSubmit={(e) => {
            e.preventDefault()
            apply()
          }}
        >
          <Field label="Сущность">
            <select className={inputCls} value={draft.entity} onChange={(e) => setDraft({ ...draft, entity: e.target.value })}>
              <option value="">Все</option>
              {(entities ?? []).map((e) => (
                <option key={e.key} value={e.key}>
                  {e.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="ID сущности" hint="Точное совпадение с идентификатором из пути запроса">
            <input
              className={inputCls}
              value={draft.entity_id}
              onChange={(e) => setDraft({ ...draft, entity_id: e.target.value })}
              placeholder="например, 3f9c…"
            />
          </Field>
          <Field label="Пользователь" hint="Часть e-mail, без учёта регистра">
            <input
              className={inputCls}
              value={draft.user_email}
              onChange={(e) => setDraft({ ...draft, user_email: e.target.value })}
              placeholder="@agency.ru"
            />
          </Field>
          <Field label="Действие">
            <select className={inputCls} value={draft.method} onChange={(e) => setDraft({ ...draft, method: e.target.value })}>
              <option value="">Любое</option>
              {Object.entries(METHOD_LABELS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </Field>
          <Field label="С даты">
            <input type="date" className={inputCls} value={draft.from} onChange={(e) => setDraft({ ...draft, from: e.target.value })} />
          </Field>
          <Field label="По дату">
            <input type="date" className={inputCls} value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
          </Field>
          <label className="flex items-center gap-2 self-end pb-1.5 text-sm">
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-input accent-accent"
              checked={draft.onlyErrors}
              onChange={(e) => setDraft({ ...draft, onlyErrors: e.target.checked })}
            />
            Только ошибки (4xx/5xx)
          </label>
          <div className="flex items-end justify-end gap-2">
            <Button type="button" variant="ghost" onClick={reset} disabled={!hasActiveFilters && draft === EMPTY_FILTERS}>
              Сбросить
            </Button>
            <Button type="submit">Применить</Button>
          </div>
        </form>
      </Card>

      <Card>
        <CardHeader
          title="Записи"
          subtitle={`Страница ${pageIdx + 1}, новые сверху`}
          action={
            <div className="flex items-center gap-2">
              <Button variant="ghost" onClick={prevPage} disabled={pageIdx === 0}>
                Назад
              </Button>
              <Button variant="ghost" onClick={nextPage} disabled={!data?.next_before_id}>
                Дальше
              </Button>
            </div>
          }
        />
        {error ? (
          <div className="px-4 pb-4 text-sm text-danger">
            Не удалось загрузить журнал: {error instanceof Error ? error.message : "ошибка запроса"}
          </div>
        ) : isLoading && !data ? (
          <div className="flex justify-center py-10">
            <Spinner />
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            title="Записей нет"
            description={hasActiveFilters ? "Под текущие фильтры ничего не попало" : "Как только кто-то что-то изменит — появится здесь"}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                  <th className="px-3 py-2 font-medium">Время</th>
                  <th className="px-3 py-2 font-medium">Пользователь</th>
                  <th className="px-3 py-2 font-medium">Действие</th>
                  <th className="px-3 py-2 font-medium">Сущность</th>
                  <th className="px-3 py-2 font-medium">Путь</th>
                  <th className="px-3 py-2 font-medium">Код</th>
                  <th className="px-3 py-2 font-medium">IP</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-b border-border/60 last:border-0">
                    <td className="whitespace-nowrap px-3 py-2.5 tabular-nums text-muted">{fmtTs(r.created_at)}</td>
                    <td className="px-3 py-2.5">
                      {r.user_email ? (
                        <div>
                          <div className="font-medium">{r.user_email}</div>
                          <div className="text-xs text-muted">{r.user_role ?? ""}</div>
                        </div>
                      ) : (
                        <span className="text-muted">без сессии</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5">
                      <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${methodTone(r.method)}`}>
                        {METHOD_LABELS[r.method] ?? r.method}
                      </span>
                    </td>
                    <td className="px-3 py-2.5">
                      <div>{entityLabel(r.entity)}</div>
                      {r.entity_id ? <div className="font-mono text-xs text-muted">{r.entity_id}</div> : null}
                    </td>
                    <td className="max-w-xs truncate px-3 py-2.5 font-mono text-xs text-muted" title={r.path}>
                      {r.path}
                    </td>
                    <td className="px-3 py-2.5">
                      <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium tabular-nums ${statusTone(r.status_code)}`}>
                        {r.status_code}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 font-mono text-xs text-muted">{r.ip ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
