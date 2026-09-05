"use client"

import { Suspense, useEffect, useMemo, useState } from "react"
import { useSearchParams } from "next/navigation"
import useSWR from "swr"
import { api, fetcher } from "@/lib/api"
import { fmtMoney, fmtNum, fmtPct } from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Field, Spinner, inputCls } from "@/components/ui"
import { ColumnsButton, ColumnsModal, useTableColumns } from "@/components/table-columns"
import { TimeseriesChart, METRIC_COLORS, CHART_TZ, TZ_NOTE } from "@/components/timeseries-chart"

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
  { key: "publisher", label: "Паблишер", section: "Инвентарь SSP" },
  { key: "tagid", label: "Плейсмент (tagid)", section: "Инвентарь SSP" },
  { key: "bundle", label: "Приложение (bundle)", section: "Инвентарь SSP" },
  { key: "loss_reason", label: "Причина проигрыша", section: "Инвентарь SSP" },
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
  { key: "viewability", label: "Viewability", fmt: fmtPct },
  { key: "losses", label: "Проигрыши (lurl)", fmt: fmtNum },
  { key: "loss_rate", label: "Loss rate", fmt: fmtPct },
  { key: "avg_bid", label: "Ср. ставка", fmt: fmtMoney },
  { key: "avg_bidfloor", label: "Ср. bidfloor", fmt: fmtMoney },
  { key: "avg_min_to_win", label: "Ср. цена победы", fmt: fmtMoney },
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
      <div className="mb-2 flex items-baseline gap-2">
        <span className="text-sm font-medium text-foreground">{title}</span>
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
  // Метрики графика динамики (клики/показы/CTR) — график строится по временной группировке
  const [chartMetrics, setChartMetrics] = useState<string[]>(["impressions", "clicks"])

  // Первая временная группировка отчёта — ось X графика
  const timeKey = useMemo(() => groupings.find((g) => ["hour", "day", "week", "month"].includes(g)), [groupings])

  /**
   * Точки графика: строки отчёта суммируются по времени (если есть другие
   * группировки — например день + кампания, — точки складываются в одну),
   * CTR пересчитывается из агрегатов.
   */
  const chartData = useMemo(() => {
    if (!timeKey || !report?.rows?.length) return []
    const byTs = new Map<string, { ts: string; impressions: number; clicks: number; ctr: number }>()
    for (const row of report.rows) {
      const ts = String(row[timeKey] ?? "")
      if (!ts) continue
      const p = byTs.get(ts) || { ts, impressions: 0, clicks: 0, ctr: 0 }
      p.impressions += Number(row.impressions ?? 0)
      p.clicks += Number(row.clicks ?? 0)
      byTs.set(ts, p)
    }
    const points = Array.from(byTs.values()).sort((a, b) => (a.ts < b.ts ? -1 : 1))
    for (const p of points) p.ctr = p.impressions ? Math.round((p.clicks / p.impressions) * 100 * 1000) / 1000 : 0
    return points
  }, [timeKey, report])

  const chartGranularity: "hour" | "day" | "week" = timeKey === "hour" ? "hour" : timeKey === "week" ? "week" : "day"

  const CHART_METRIC_OPTIONS = [
    { key: "impressions", label: "Показы" },
    { key: "clicks", label: "Клики" },
    { key: "ctr", label: "CTR" },
  ]

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

  // Бакеты времени приходят в UTC (date_trunc в БД) — форматируем в UTC, чтобы
  // «день» не сдвигался на дату браузера.
  function fmtDim(key: string, value: string): string {
    if (!value || value === "—") return "—"
    if (key === "hour") {
      const d = new Date(value)
      return isNaN(d.getTime())
        ? value
        : d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: CHART_TZ })
    }
    if (key === "day" || key === "week" || key === "month") {
      const d = new Date(value)
      if (isNaN(d.getTime())) return value
      if (key === "month") return d.toLocaleDateString("ru-RU", { month: "long", year: "numeric", timeZone: CHART_TZ })
      return d.toLocaleDateString("ru-RU", { timeZone: CHART_TZ })
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
        <div className="space-y-4 p-4">
          {/* Период и фильтры — единая сетка полей, как в настройках кампании */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Период">
              <select
                className={inputCls}
                value={preset || "custom"}
                onChange={(e) => {
                  const key = e.target.value
                  if (key === "custom") {
                    setPreset("")
                  } else {
                    applyPreset(key)
                  }
                }}
              >
                {PRESETS.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.label}
                  </option>
                ))}
                <option value="custom">Свой период</option>
              </select>
            </Field>
            <Field label="С даты">
              <input
                type="date"
                className={inputCls}
                value={dateFrom}
                max={dateTo || undefined}
                onChange={(e) => {
                  setDateFrom(e.target.value)
                  setPreset("")
                }}
              />
            </Field>
            <Field label="По дату">
              <input
                type="date"
                className={inputCls}
                value={dateTo}
                min={dateFrom || undefined}
                onChange={(e) => {
                  setDateTo(e.target.value)
                  setPreset("")
                }}
              />
            </Field>
            <Field label="Группа кампаний">
              <select className={inputCls} value={groupId} onChange={(e) => setGroupId(e.target.value)}>
                <option value="">Все группы</option>
                {(groups || []).map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          {/* Кампании */}
          <div className="space-y-2">
            <span className="text-sm font-medium text-foreground">
              Кампании <span className="font-normal text-muted">(пусто — все)</span>
            </span>
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

          {/* Группировки */}
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

      {/* График динамики: строится по временной группировке отчёта */}
      {ranOnce && timeKey && chartData.length > 0 ? (
        <Card>
          <CardHeader
            title="Динамика"
            subtitle={`По группировке «${GROUPINGS.find((g) => g.key === timeKey)?.label || timeKey}» · ${TZ_NOTE}`}
            action={
              <div className="flex flex-wrap gap-1.5" role="group" aria-label="Метрики графика">
                {CHART_METRIC_OPTIONS.map((m) => {
                  const active = chartMetrics.includes(m.key)
                  return (
                    <button
                      key={m.key}
                      type="button"
                      aria-pressed={active}
                      onClick={() =>
                        setChartMetrics((prev) => {
                          if (prev.includes(m.key)) return prev.length > 1 ? prev.filter((k) => k !== m.key) : prev
                          return [...prev, m.key]
                        })
                      }
                      className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                        active
                          ? "border-transparent bg-foreground text-background"
                          : "border-border bg-transparent text-muted hover:text-foreground"
                      }`}
                    >
                      <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: METRIC_COLORS[m.key] }} />
                      {m.label}
                    </button>
                  )
                })}
              </div>
            }
          />
          <div className="px-2 pb-2">
            <TimeseriesChart data={chartData} metrics={chartMetrics} granularity={chartGranularity} />
          </div>
        </Card>
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
                        {row[m.key] == null ? "—" : m.fmt(Number(row[m.key]))}
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
