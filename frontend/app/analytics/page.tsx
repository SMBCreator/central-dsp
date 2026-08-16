"use client"

import { useState } from "react"
import useSWR from "swr"
import { fetcher } from "@/lib/api"
import {
  fmtMoney,
  fmtNum,
  fmtPct,
  type BreakdownRow,
  type StatsSummary,
  type TimeseriesPoint,
} from "@/lib/types"
import { Card, CardHeader, EmptyState, KpiCard, Spinner, Table } from "@/components/ui"
import { TimeseriesChart } from "@/components/timeseries-chart"

const DIMENSIONS = [
  { key: "campaign", label: "По кампаниям" },
  { key: "supply_partner", label: "По SSP" },
  { key: "geo", label: "По гео" },
  { key: "device", label: "По устройствам" },
  { key: "ad_type", label: "По форматам" },
] as const

export default function AnalyticsPage() {
  const [hours, setHours] = useState(168)
  const [dim, setDim] = useState<(typeof DIMENSIONS)[number]["key"]>("campaign")

  const { data: summary } = useSWR<StatsSummary>(`/stats/summary?hours=${hours}`, fetcher)
  const { data: series } = useSWR<TimeseriesPoint[]>(`/stats/timeseries?hours=${hours}`, fetcher)
  const { data: breakdown } = useSWR<BreakdownRow[]>(`/stats/breakdown?hours=${hours}&dimension=${dim}`, fetcher)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Аналитика</h1>
          <p className="mt-0.5 text-sm text-muted">Детальные отчёты по закупке трафика</p>
        </div>
        <div className="flex gap-1 rounded-md border border-border bg-card p-0.5">
          {[24, 168, 720].map((h) => (
            <button
              key={h}
              type="button"
              onClick={() => setHours(h)}
              className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
                hours === h ? "bg-primary text-primary-foreground" : "text-muted hover:text-foreground"
              }`}
            >
              {h === 24 ? "Сутки" : h === 168 ? "Неделя" : "Месяц"}
            </button>
          ))}
        </div>
      </div>

      {summary ? (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <KpiCard label="Расход" value={fmtMoney(summary.spend)} hint={`eCPM ${fmtMoney(summary.cpm)}`} />
          <KpiCard label="Ставки → Выигрыши" value={`${fmtNum(summary.bids)} → ${fmtNum(summary.wins)}`} hint={`Win rate ${fmtPct(summary.win_rate)}`} />
          <KpiCard label="Показы" value={fmtNum(summary.impressions)} />
          <KpiCard label="Клики" value={fmtNum(summary.clicks)} hint={`CTR ${fmtPct(summary.ctr)}`} />
        </div>
      ) : (
        <Spinner />
      )}

      <Card>
        <CardHeader title="Расход по времени" />
        <div className="p-4">
          {series ? (
            series.length > 0 ? (
              <TimeseriesChart data={series} metric="spend" color="var(--color-warning)" label="Расход" />
            ) : (
              <EmptyState title="Нет данных за выбранный период" />
            )
          ) : (
            <Spinner />
          )}
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Разбивка"
          action={
            <div className="flex flex-wrap gap-1">
              {DIMENSIONS.map((d) => (
                <button
                  key={d.key}
                  type="button"
                  onClick={() => setDim(d.key)}
                  className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                    dim === d.key ? "bg-primary/10 text-primary" : "text-muted hover:text-foreground"
                  }`}
                >
                  {d.label}
                </button>
              ))}
            </div>
          }
        />
        {breakdown ? (
          breakdown.length > 0 ? (
            <Table headers={["Сегмент", "Ставки", "Выигрыши", "Показы", "Клики", "CTR", "Расход"]}>
              {breakdown.map((row) => (
                <tr key={row.key} className="hover:bg-surface">
                  <td className="px-4 py-3 font-medium text-foreground">{row.name}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtNum(row.bids)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtNum(row.wins)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtNum(row.impressions)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtNum(row.clicks)}</td>
                  <td className="px-4 py-3 font-mono text-xs">
                    {row.impressions > 0 ? fmtPct(row.clicks / row.impressions) : "—"}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtMoney(row.spend)}</td>
                </tr>
              ))}
            </Table>
          ) : (
            <EmptyState title="Нет данных" description="Запустите трафик, чтобы увидеть разбивку." />
          )
        ) : (
          <Spinner />
        )}
      </Card>
    </div>
  )
}
