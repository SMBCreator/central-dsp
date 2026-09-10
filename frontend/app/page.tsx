"use client"

import { useMemo, useState } from "react"
import useSWR from "swr"
import Link from "next/link"
import { fetcher } from "@/lib/api"
import {
  fmtMoney,
  fmtNum,
  fmtPct,
  type Campaign,
  type StatsSummary,
  type TimeseriesPoint,
} from "@/lib/types"
import { Card, CardHeader, ChartCard, KpiCard, PillTabs, Spinner, StatusBadge, Table, EmptyState, Button, inputCls } from "@/components/ui"
import { TimeseriesChart, METRIC_COLORS, TZ_NOTE, type MetricKey } from "@/components/timeseries-chart"

const METRICS = [
  { key: "impressions", label: "Показы" },
  { key: "clicks", label: "Клики" },
  { key: "spend", label: "Расход" },
  { key: "bids", label: "Ставки" },
] as const

const PERIODS = [
  { key: "24", label: "24 часа" },
  { key: "72", label: "3 дня" },
  { key: "168", label: "7 дней" },
  { key: "720", label: "30 дней" },
  { key: "custom", label: "Свои даты" },
] as const

const GRANULARITIES = [
  { key: "hour", label: "По часам" },
  { key: "day", label: "По дням" },
  { key: "week", label: "По неделям" },
] as const

type Granularity = (typeof GRANULARITIES)[number]["key"]

/** Дефолтная гранулярность под период: сутки — часы, до месяца — дни, дальше — недели. */
function defaultGranularity(hours: number): Granularity {
  if (hours <= 72) return "hour"
  if (hours <= 720) return "day"
  return "week"
}

function isoDaysAgo(days: number): string {
  const d = new Date(Date.now() - days * 864e5)
  return d.toISOString().slice(0, 10)
}

export default function DashboardPage() {
  const [period, setPeriod] = useState<(typeof PERIODS)[number]["key"]>("24")
  const [dateFrom, setDateFrom] = useState(isoDaysAgo(7))
  const [dateTo, setDateTo] = useState(isoDaysAgo(0))
  // Поповер выбора своего периода — открывается при клике на «Свои даты»
  const [customOpen, setCustomOpen] = useState(false)
  // null = «авто» (подбирается под период), явный выбор пользователя сохраняется
  const [granularityOverride, setGranularityOverride] = useState<Granularity | null>(null)
  const [activeMetrics, setActiveMetrics] = useState<MetricKey[]>(["impressions"])
  // H6: автообновление — байер держит вкладку открытой весь день
  const [autoRefresh, setAutoRefresh] = useState(true)
  const refreshInterval = autoRefresh ? 30000 : 0

  const isCustom = period === "custom"
  const hours = isCustom ? 168 : Number(period)
  const customDays = useMemo(() => {
    const from = new Date(dateFrom).getTime()
    const to = new Date(dateTo).getTime()
    return Number.isFinite(from) && Number.isFinite(to) ? Math.max(1, Math.round((to - from) / 864e5) + 1) : 7
  }, [dateFrom, dateTo])
  const granularity: Granularity =
    granularityOverride ?? defaultGranularity(isCustom ? customDays * 24 : hours)

  const customValid = isCustom ? Boolean(dateFrom && dateTo && dateFrom <= dateTo) : true
  const seriesQuery = isCustom
    ? `/stats/timeseries?date_from=${dateFrom}&date_to=${dateTo}&granularity=${granularity}`
    : `/stats/timeseries?hours=${hours}&granularity=${granularity}`
  const summaryQuery = isCustom
    ? `/stats/summary?hours=${customDays * 24}`
    : `/stats/summary?hours=${hours}`

  const { data: summary } = useSWR<StatsSummary>(summaryQuery, fetcher, { refreshInterval })
  const { data: series } = useSWR<TimeseriesPoint[]>(customValid ? seriesQuery : null, fetcher, { refreshInterval })
  const { data: campaigns } = useSWR<Campaign[]>("/campaigns", fetcher, { refreshInterval })

  const topCampaigns = (campaigns || []).slice(0, 5)

  const toggleMetric = (key: MetricKey) => {
    setActiveMetrics((prev) => {
      if (prev.includes(key)) {
        // Последнюю метрику не снимаем — график не должен остаться пустым
        return prev.length > 1 ? prev.filter((m) => m !== key) : prev
      }
      return [...prev, key]
    })
  }

  const periodLabel = isCustom
    ? `${customDays} дн.`
    : hours === 24
      ? "за сутки"
      : hours === 72
        ? "за 3 дня"
        : hours === 168
          ? "за неделю"
          : "за 30 дней"

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Дашборд</h1>
          <p className="mt-0.5 text-sm text-muted">Обзор эффективности закупки в реальном времени</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="h-3.5 w-3.5 accent-accent"
            />
            Автообновление 30 сек
          </label>
          <div className="relative">
            <PillTabs
              value={period}
              onChange={(v) => {
                setPeriod(v)
                setCustomOpen(v === "custom")
              }}
              options={PERIODS}
            />
            {isCustom && customOpen ? (
              <div className="absolute right-0 top-full z-30 mt-2 w-64 space-y-2.5 rounded-xl border border-border bg-card p-3 shadow-lg">
                <div>
                  <label htmlFor="dash-from" className="mb-1 block text-xs text-muted">
                    С даты
                  </label>
                  <input
                    id="dash-from"
                    type="date"
                    value={dateFrom}
                    max={dateTo || undefined}
                    onChange={(e) => setDateFrom(e.target.value)}
                    className={inputCls}
                  />
                </div>
                <div>
                  <label htmlFor="dash-to" className="mb-1 block text-xs text-muted">
                    По дату
                  </label>
                  <input
                    id="dash-to"
                    type="date"
                    value={dateTo}
                    min={dateFrom || undefined}
                    onChange={(e) => setDateTo(e.target.value)}
                    className={inputCls}
                  />
                </div>
                {!customValid ? (
                  <p className="text-xs text-danger">Дата начала не может быть позже даты конца</p>
                ) : null}
                <div className="flex justify-end">
                  <Button variant="secondary" onClick={() => setCustomOpen(false)} disabled={!customValid}>
                    Готово
                  </Button>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {summary ? (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <KpiCard label="Расход" value={fmtMoney(summary.spend)} hint={`eCPM ${fmtMoney(summary.ecpm)}`} />
          <KpiCard
            label="Показы"
            value={fmtNum(summary.impressions)}
            hint={
              summary.billable_imps != null
                ? `${fmtNum(summary.wins)} выигрышей · ${fmtNum(summary.billable_imps)} оплачено`
                : `${fmtNum(summary.wins)} выигрышей`
            }
          />
          <KpiCard label="Клики" value={fmtNum(summary.clicks)} hint={`CTR ${fmtPct(summary.ctr)}`} />
          <KpiCard label="Win Rate" value={fmtPct(summary.win_rate)} hint={`${fmtNum(summary.bids)} ставок`} />
        </div>
      ) : (
        <Spinner />
      )}

      <ChartCard
        label="Динамика"
        badge={activeMetrics.map((m) => METRICS.find((x) => x.key === m)?.label).join(" + ")}
        metric={
          summary && activeMetrics.length === 1
            ? activeMetrics[0] === "spend"
              ? fmtMoney(summary.spend)
              : fmtNum(summary[activeMetrics[0] as keyof StatsSummary] as number)
            : undefined
        }
        metricUnit={periodLabel}
        footerLeft={`${series?.length || 0} точек данных · ${TZ_NOTE}`}
        footerRight="Central DSP"
      >
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-2 pt-2">
          <PillTabs
            value={granularity}
            onChange={(v) => setGranularityOverride(v)}
            options={GRANULARITIES}
          />
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Метрики графика">
            {METRICS.map((m) => {
              const active = activeMetrics.includes(m.key)
              return (
                <button
                  key={m.key}
                  type="button"
                  aria-pressed={active}
                  onClick={() => toggleMetric(m.key)}
                  className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                    active
                      ? "border-transparent bg-foreground text-background"
                      : "border-border bg-transparent text-muted hover:text-foreground"
                  }`}
                >
                  <span
                    aria-hidden
                    className="h-2 w-2 rounded-full"
                    style={{ background: METRIC_COLORS[m.key] }}
                  />
                  {m.label}
                </button>
              )
            })}
          </div>
        </div>
        {series ? (
          series.length > 0 ? (
            <TimeseriesChart data={series} metrics={activeMetrics} granularity={granularity} />
          ) : (
            <EmptyState
              title="Пока нет данных"
              description="Запустите кампанию или отправьте тестовый трафик через RTB-тестер."
              action={
                <Link href="/rtb-test">
                  <Button variant="secondary">Открыть RTB-тестер</Button>
                </Link>
              }
            />
          )
        ) : (
          <Spinner />
        )}
      </ChartCard>

      <Card>
        <CardHeader
          title="Кампании"
          action={
            <Link href="/campaigns" className="text-xs font-medium text-accent hover:underline">
              Все кампании
            </Link>
          }
        />
        {campaigns ? (
          topCampaigns.length > 0 ? (
            <Table headers={["Название", "Статус", "Дневной бюджет", "Расход сегодня", "Расход всего"]}>
              {topCampaigns.map((c) => (
                <tr key={c.id} className="hover:bg-surface">
                  <td className="px-4 py-3">
                    <Link href={`/campaigns/${c.id}`} className="font-medium text-foreground hover:text-accent">
                      {c.name}
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={c.status} />
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtMoney(c.daily_budget)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtMoney(c.spent_today)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtMoney(c.spent_total)}</td>
                </tr>
              ))}
            </Table>
          ) : (
            <EmptyState
              title="Нет кампаний"
              description="Создайте первую кампанию, чтобы начать закупку трафика."
              action={
                <Link href="/campaigns">
                  <Button>Создать кампанию</Button>
                </Link>
              }
            />
          )
        ) : (
          <Spinner />
        )}
      </Card>
    </div>
  )
}
