"use client"

import { useState } from "react"
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
import { Card, CardHeader, ChartCard, KpiCard, PillTabs, Spinner, StatusBadge, Table, EmptyState, Button } from "@/components/ui"
import { TimeseriesChart } from "@/components/timeseries-chart"

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
] as const

export default function DashboardPage() {
  const [hours, setHours] = useState(24)
  const [metric, setMetric] = useState<(typeof METRICS)[number]["key"]>("impressions")
  // H6: автообновление — байер держит вкладку открытой весь день
  const [autoRefresh, setAutoRefresh] = useState(true)
  const refreshInterval = autoRefresh ? 30000 : 0

  const { data: summary } = useSWR<StatsSummary>(`/stats/summary?hours=${hours}`, fetcher, { refreshInterval })
  const { data: series } = useSWR<TimeseriesPoint[]>(`/stats/timeseries?hours=${hours}`, fetcher, { refreshInterval })
  const { data: campaigns } = useSWR<Campaign[]>("/campaigns", fetcher, { refreshInterval })

  const activeMetric = METRICS.find((m) => m.key === metric)!
  const topCampaigns = (campaigns || []).slice(0, 5)

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
          <PillTabs
            value={String(hours) as (typeof PERIODS)[number]["key"]}
            onChange={(v) => setHours(Number(v))}
            options={PERIODS}
          />
        </div>
      </div>

      {summary ? (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <KpiCard label="Расход" value={fmtMoney(summary.spend)} hint={`eCPM ${fmtMoney(summary.ecpm)}`} />
          <KpiCard label="Показы" value={fmtNum(summary.impressions)} hint={`${fmtNum(summary.wins)} выигрышей`} />
          <KpiCard label="Клики" value={fmtNum(summary.clicks)} hint={`CTR ${fmtPct(summary.ctr)}`} />
          <KpiCard label="Win Rate" value={fmtPct(summary.win_rate)} hint={`${fmtNum(summary.bids)} ставок`} />
        </div>
      ) : (
        <Spinner />
      )}

      <ChartCard
        label="Динамика"
        badge={activeMetric.label}
        metric={
          summary
            ? metric === "spend"
              ? fmtMoney(summary.spend)
              : fmtNum(summary[metric as keyof StatsSummary] as number)
            : undefined
        }
        metricUnit={`за ${hours === 24 ? "сутки" : hours === 72 ? "3 дня" : "неделю"}`}
        footerLeft={`${series?.length || 0} точек данных`}
        footerRight="Central DSP"
      >
        <div className="mb-3 flex justify-end px-2 pt-2">
          <PillTabs value={metric} onChange={setMetric} options={METRICS} />
        </div>
        {series ? (
          series.length > 0 ? (
            <TimeseriesChart data={series} metric={metric} label={activeMetric.label} />
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
