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
import { Card, CardHeader, KpiCard, Spinner, StatusBadge, Table, EmptyState, Button } from "@/components/ui"
import { TimeseriesChart } from "@/components/timeseries-chart"

const METRICS = [
  { key: "impressions", label: "Показы", color: "var(--color-accent)" },
  { key: "clicks", label: "Клики", color: "var(--color-success)" },
  { key: "spend", label: "Расход", color: "var(--color-warning)" },
  { key: "bids", label: "Ставки", color: "var(--color-primary)" },
] as const

export default function DashboardPage() {
  const [hours, setHours] = useState(24)
  const [metric, setMetric] = useState<(typeof METRICS)[number]["key"]>("impressions")

  const { data: summary } = useSWR<StatsSummary>(`/stats/summary?hours=${hours}`, fetcher)
  const { data: series } = useSWR<TimeseriesPoint[]>(`/stats/timeseries?hours=${hours}`, fetcher)
  const { data: campaigns } = useSWR<Campaign[]>("/campaigns", fetcher)

  const activeMetric = METRICS.find((m) => m.key === metric)!
  const topCampaigns = (campaigns || []).slice(0, 5)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Дашборд</h1>
          <p className="mt-0.5 text-sm text-muted">Обзор эффективности закупки в реальном времени</p>
        </div>
        <div className="flex gap-1 rounded-md border border-border bg-card p-0.5">
          {[24, 72, 168].map((h) => (
            <button
              key={h}
              type="button"
              onClick={() => setHours(h)}
              className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
                hours === h ? "bg-primary text-primary-foreground" : "text-muted hover:text-foreground"
              }`}
            >
              {h === 24 ? "24 часа" : h === 72 ? "3 дня" : "7 дней"}
            </button>
          ))}
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

      <Card>
        <CardHeader
          title="Динамика"
          action={
            <div className="flex gap-1">
              {METRICS.map((m) => (
                <button
                  key={m.key}
                  type="button"
                  onClick={() => setMetric(m.key)}
                  className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                    metric === m.key ? "bg-primary/10 text-primary" : "text-muted hover:text-foreground"
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </div>
          }
        />
        <div className="p-4">
          {series ? (
            series.length > 0 ? (
              <TimeseriesChart data={series} metric={metric} color={activeMetric.color} label={activeMetric.label} />
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
        </div>
      </Card>

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
