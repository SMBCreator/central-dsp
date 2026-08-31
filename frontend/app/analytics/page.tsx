"use client"

import { Suspense, useEffect, useMemo, useState } from "react"
import { useSearchParams } from "next/navigation"
import useSWR from "swr"
import { api, fetcher } from "@/lib/api"
import { fmtMoney, fmtNum, fmtPct } from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Spinner, inputCls } from "@/components/ui"
import { DomainQualityCard } from "@/components/domain-quality-card"
import { ColumnsButton, ColumnsModal, useTableColumns } from "@/components/table-columns"

type Campaign = { id: string; name: string }
type Group = { id: string; name: string }

type ReportRow = Record<string, string | number>
type ReportResponse = {
  rows: ReportRow[]
  totals: Record<string, number> | null
}

const GROUPINGS: { key: string; label: string; section: string }[] = [
  { key: "hour", label: "Час", section: "Время" },
  { key: "day", label: "День", section: "Время" },
  { key: "week", label: "Неделя", section: "Время" },
  { key: "month", label: "Месяц", section: "Время" },
  { key: "campaign", label: "Кампания", section: "Рекламодатель" },
  { key: "creative", label: "Креатив", section: "Рекламодатель" },
  { key: "supply_partner", label: "SSP-партнёр", section: "Трафик" },
  { key: "country", label: "Страна", section: "Трафик" },
  { key: "device", label: "Тип устройства", section: "Трафик" },
  { key: "os", label: "ОС", section: "Трафик" },
  { key: "browser", label: "Браузер", section: "Трафик" },
  { key: "ad_type", label: "Формат", section: "Трафик" },
  { key: "domain", label: "Домен", section: "Трафик" },
]

const METRICS: { key: string; label: string; fmt: (v: number) => string }[] = [
  { key: "bids", label: "Ставки", fmt: fmtNum },
  { key: "wins", label: "Выигрыши", fmt: fmtNum },
  { key: "win_rate", label: "Win rate", fmt: fmtPct },
  { key: "impressions", label: "Показы", fmt: fmtNum },
  { key: "clicks", label: "Клики", fmt: fmtNum },
  { key: "ctr", label: "CTR", fmt: fmtPct },
  { key: "spend", label: "Потрачено", fmt: fmtMoney },
  { key: "cpm", label: "CPM", fmt: fmtMoney },
  { key: "cpc", label: "CPC", fmt: fmtMoney },
  { key: "conversions", label: "Конверсии", fmt: fmtNum },
  { key: "cpa", label: "CPA", fmt: fmtMoney },
]

const DEFAULT_METRICS = ["impressions", "clicks", "ctr", "spend", "cpm", "win_rate"]

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

const PRESETS: { key: string; label: string; range: () => [string, string] }[] = [
  { key: "today", label: "Сегодня", range: () => [isoDate(new Date()), isoDate(new Date())] },
  {
    key: "yesterday",
    label: "Вчера",
    range: () => {
      const d = new Date(Date.now() - 86400000)
      return [isoDate(d), isoDate(d)]
    },
  },
  {
    key: "7d",
    label: "За 7 дней",
    range: () => [isoDate(new Date(Date.now() - 6 * 86400000)), isoDate(new Date())],
  },
  {
    key: "30d",
    label: "За 30 дней",
    range: () => [isoDate(new Date(Date.now() - 29 * 86400000)), isoDate(new Date())],
  },
]

function ChipPicker({
  title,
  options,
  selected,
  onToggle,
  hint,
}: {
  title: string
  options: { key: string; label: string; section?: string }[]
  selected: string[]
  onToggle: (key: string) => void
  hint?: string
}) {
  const sections = useMemo(() => {
    const map = new Map<string, { key: string; label: string }[]>()
    for (const o of options) {
      const s = o.section || ""
      if (!map.has(s)) map.set(s, [])
      map.get(s)!.push(o)
    }
    return Array.from(map.entries())
  }, [options])

  return (
    <div>
      <div className="mb-1.5 flex items-baseline gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</span>
        {hint ? <span className="text-xs text-muted">{hint}</span> : null}
      </div>
      <div className="space-y-2">
        {sections.map(([section, opts]) => (
          <div key={section} className="flex flex-wrap items-center gap-1.5">
            {section ? <span className="mr-1 text-xs text-muted">{section}:</span> : null}
            {opts.map((o) => {
              const active = selected.includes(o.key)
              return (
                <button
                  key={o.key}
                  type="button"
                  onClick={() => onToggle(o.key)}
                  className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                    active
                      ? "border-accent bg-accent/10 font-medium text-accent"
                      : "border-border bg-surface text-muted hover:border-accent/40 hover:text-foreground"
                  }`}
                >
                  {o.label}
                </button>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}

function AnalyticsInner() {
  const params = useSearchParams()
  const urlCampaign = params.get("campaign_id")
  const urlGroup = params.get("group_id")

  const { data: campaigns } = useSWR<Campaign[]>("/campaigns", fetcher)
  const { data: groups } = useSWR<Group[]>("/groups", fetcher)

  const [preset, setPreset] = useState("7d")
  const [dateFrom, setDateFrom] = useState(() => PRESETS[2].range()[0])
  const [dateTo, setDateTo] = useState(() => PRESETS[2].range()[1])
  const [campaignIds, setCampaignIds] = useState<string[]>(urlCampaign ? [urlCampaign] : [])
  const [groupId, setGroupId] = useState<string>(urlGroup || "")
  const [groupings, setGroupings] = useState<string[]>(["day"])
  // метрики = настраиваемые столбцы результата (единый вид с «Кампаниями»)
  const [metrics, setMetrics] = useTableColumns(
    "dsp_analytics_metrics_v1",
    METRICS.map((m) => m.key),
    DEFAULT_METRICS,
  )
  const [colsOpen, setColsOpen] = useState(false)

  const [report, setReport] = useState<ReportResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [ranOnce, setRanOnce] = useState(false)

  async function run() {
    setLoading(true)
    setError(null)
    try {
      const res = await api<ReportResponse>("/stats/report", {
        method: "POST",
        body: {
          date_from: dateFrom,
          date_to: dateTo,
          groupings,
          campaign_ids: campaignIds,
          group_id: groupId || null,
        },
      })
      setReport(res)
      setRanOnce(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось построить отчёт")
    } finally {
      setLoading(false)
    }
  }

  // auto-run when arriving with a campaign/group link
  useEffect(() => {
    if ((urlCampaign || urlGroup) && !ranOnce) void run()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlCampaign, urlGroup])

  function applyPreset(key: string) {
    const p = PRESETS.find((x) => x.key === key)
    if (!p) return
    setPreset(key)
    const [f, t] = p.range()
    setDateFrom(f)
    setDateTo(t)
  }

  function toggle(list: string[], set: (v: string[]) => void, key: string, min = 0) {
    if (list.includes(key)) {
      if (list.length > min) set(list.filter((k) => k !== key))
    } else {
      set([...list, key])
    }
  }

  // порядок метрик = порядок в настройке столбцов
  const activeMetrics = metrics
    .map((k) => METRICS.find((m) => m.key === k))
    .filter(Boolean) as typeof METRICS
  const activeGroupings = GROUPINGS.filter((g) => groupings.includes(g.key))

  function fmtDim(key: string, value: string): string {
    if (!value || value === "��") return "—"
    if (key === "hour") {
      const d = new Date(value)
      return isNaN(d.getTime()) ? value : d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
    }
    if (key === "day" || key === "week" || key === "month") {
      const d = new Date(value)
      if (isNaN(d.getTime())) return value
      if (key === "month") return d.toLocaleDateString("ru-RU", { month: "long", year: "numeric" })
      return d.toLocaleDateString("ru-RU")
    }
    return value
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Статистика</h1>
          <p className="mt-0.5 text-sm text-muted">
            Конструктор отчётов: настройте фильтры, группировки и метрики, затем нажмите «Обновить»
          </p>
        </div>
        <Button onClick={run} disabled={loading}>
          {loading ? "Строим отчёт..." : "Обновить"}
        </Button>
      </div>

      <Card>
        <CardHeader title="Параметры отчёта" subtitle="Период, фильтры и группировки" />
        <div className="space-y-5 px-4 pb-4">
          {/* Period */}
          <div className="flex flex-wrap items-center gap-2">
            {PRESETS.map((p) => (
              <button
                key={p.key}
                type="button"
                onClick={() => applyPreset(p.key)}
                className={`rounded-lg border px-3 py-1.5 text-sm transition-colors ${
                  preset === p.key
                    ? "border-accent bg-accent/10 font-medium text-accent"
                    : "border-border bg-surface text-muted hover:text-foreground"
                }`}
              >
                {p.label}
              </button>
            ))}
            <div className="flex items-center gap-2">
              <input
                type="date"
                className={inputCls + " w-auto"}
                value={dateFrom}
                onChange={(e) => {
                  setDateFrom(e.target.value)
                  setPreset("")
                }}
              />
              <span className="text-sm text-muted">—</span>
              <input
                type="date"
                className={inputCls + " w-auto"}
                value={dateTo}
                onChange={(e) => {
                  setDateTo(e.target.value)
                  setPreset("")
                }}
              />
            </div>
          </div>

          {/* Filters */}
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-muted">
                Кампании (пусто = все)
              </label>
              <div className="flex max-h-36 flex-wrap gap-1.5 overflow-y-auto rounded-lg border border-border bg-surface p-2">
                {(campaigns || []).map((c) => {
                  const active = campaignIds.includes(c.id)
                  return (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => toggle(campaignIds, setCampaignIds, c.id)}
                      className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                        active
                          ? "border-accent bg-accent/10 font-medium text-accent"
                          : "border-border bg-background text-muted hover:text-foreground"
                      }`}
                    >
                      {c.name}
                    </button>
                  )
                })}
                {campaigns && campaigns.length === 0 ? (
                  <span className="px-1 py-0.5 text-xs text-muted">Нет кампаний</span>
                ) : null}
              </div>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-muted">
                Группа кампаний
              </label>
              <select className={inputCls} value={groupId} onChange={(e) => setGroupId(e.target.value)}>
                <option value="">Все группы</option>
                {(groups || []).map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Groupings */}
          <ChipPicker
            title="Группировки"
            hint="до 4 одновременно"
            options={GROUPINGS}
            selected={groupings}
            onToggle={(k) => {
              if (!groupings.includes(k) && groupings.length >= 4) return
              toggle(groupings, setGroupings, k, 1)
            }}
          />

        </div>
      </Card>

      {error ? (
        <div className="rounded-lg border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">{error}</div>
      ) : null}

      <Card>
        <CardHeader
          title="Результат"
          subtitle={
            report?.rows?.length
              ? `${report.rows.length} строк · ${dateFrom} — ${dateTo}`
              : undefined
          }
          action={<ColumnsButton onClick={() => setColsOpen(true)} />}
        />
        <ColumnsModal
          open={colsOpen}
          onClose={() => setColsOpen(false)}
          all={METRICS.map((m) => ({ key: m.key, label: m.label }))}
          locked={activeGroupings.map((g) => g.label)}
          value={metrics}
          onSave={(keys) => setMetrics(keys.length ? keys : DEFAULT_METRICS)}
        />
        {loading ? (
          <div className="p-8">
            <Spinner />
          </div>
        ) : !ranOnce ? (
          <EmptyState
            title="Отчёт ещё не построен"
            description="Настройте фильтры и нажмите «Обновить», чтобы увидеть статистику."
          />
        ) : !report?.rows?.length ? (
          <EmptyState
            title="Нет данных за выбранный период"
            description="Попробуйте расширить период или изменить фильтры."
          />
        ) : (
          /* Метрики скроллятся горизонтально, первая группировка закреплена слева */
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                  {activeGroupings.map((g, gi) => (
                    <th
                      key={g.key}
                      className={`whitespace-nowrap px-4 py-2.5 font-medium ${gi === 0 ? "sticky left-0 z-10 min-w-[140px] bg-card" : ""}`}
                    >
                      {g.label}
                    </th>
                  ))}
                  {activeMetrics.map((m) => (
                    <th key={m.key} className="whitespace-nowrap px-4 py-2.5 text-right font-medium">
                      {m.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {report.rows.map((row, i) => (
                  <tr key={i} className="group border-b border-border/50">
                    {activeGroupings.map((g, gi) => (
                      <td
                        key={g.key}
                        className={`whitespace-nowrap px-4 py-2.5 font-medium text-foreground transition-colors group-hover:bg-surface ${gi === 0 ? "sticky left-0 z-10 min-w-[140px] bg-card" : ""}`}
                      >
                        {fmtDim(g.key, String(row[g.key] ?? "—"))}
                      </td>
                    ))}
                    {activeMetrics.map((m) => (
                      <td key={m.key} className="whitespace-nowrap px-4 py-2.5 text-right font-mono text-xs transition-colors group-hover:bg-surface">
                        {m.fmt(Number(row[m.key] ?? 0))}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
              {report.totals ? (
                <tfoot>
                  <tr className="bg-surface font-medium">
                    <td
                      className="sticky left-0 z-10 whitespace-nowrap bg-surface px-4 py-2.5 text-xs uppercase tracking-wide text-muted"
                      colSpan={activeGroupings.length}
                    >
                      Итого
                    </td>
                    {activeMetrics.map((m) => (
                      <td key={m.key} className="whitespace-nowrap px-4 py-2.5 text-right font-mono text-xs text-foreground">
                        {m.fmt(Number(report.totals![m.key] ?? 0))}
                      </td>
                    ))}
                  </tr>
                </tfoot>
              ) : null}
            </table>
          </div>
        )}
      </Card>

      <DomainQualityCard />
    </div>
  )
}

export default function AnalyticsPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <AnalyticsInner />
    </Suspense>
  )
}
