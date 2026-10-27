"use client"

import { use, useState } from "react"
import useSWR from "swr"
import { fetcher } from "@/lib/api"
import { fmtMoney, fmtNum, fmtPct } from "@/lib/types"
import { Card, CardHeader, KpiCard, Spinner, StatusBadge, Table } from "@/components/ui"
import { TimeseriesChart } from "@/components/timeseries-chart"

interface PublicReport {
  brand: { name: string; color: string | null; logo_url: string | null }
  report_name: string
  summary: {
    bids: number
    wins: number
    impressions: number
    clicks: number
    spend: number
    win_rate: number
    ctr: number
    ecpm: number
  }
  series: { ts: string; bids: number; wins: number; impressions: number; clicks: number; spend: number }[]
  campaigns: {
    id: string
    name: string
    status: string
    impressions: number
    clicks: number
    spend: number
    ctr: number
  }[]
}

const PERIODS = [
  { hours: 24, label: "24 часа" },
  { hours: 168, label: "7 дней" },
  { hours: 720, label: "30 дней" },
]

export default function PublicReportPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params)
  const [hours, setHours] = useState(168)
  const { data, error } = useSWR<PublicReport>(`/share/public/${token}?hours=${hours}`, fetcher)

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <div className="text-center">
          <p className="text-lg font-semibold text-foreground">Отчёт недоступен</p>
          <p className="mt-1 text-sm text-muted">{error instanceof Error ? error.message : "Ссылка не найдена или отозвана"}</p>
        </div>
      </div>
    )
  }
  if (!data) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    )
  }

  const accent = data.brand.color || undefined
  const s = data.summary

  return (
    <div className="min-h-screen">
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-6 py-4">
          <div className="flex items-center gap-3">
            {data.brand.logo_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={data.brand.logo_url} alt={data.brand.name} className="h-9 w-9 rounded-full object-cover" />
            ) : (
              <div
                className="flex h-9 w-9 items-center justify-center rounded-full bg-primary"
                style={accent ? { backgroundColor: accent } : undefined}
              >
                <span className="text-sm font-bold text-primary-foreground">{data.brand.name.charAt(0)}</span>
              </div>
            )}
            <div>
              <p className="text-sm font-semibold text-foreground">{data.brand.name}</p>
              <p className="text-xs text-muted">{data.report_name}</p>
            </div>
          </div>
          <div className="flex gap-1.5">
            {PERIODS.map((p) => (
              <button
                key={p.hours}
                type="button"
                onClick={() => setHours(p.hours)}
                className={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
                  hours === p.hours ? "bg-primary text-primary-foreground" : "bg-panel text-muted hover:text-foreground"
                }`}
                style={hours === p.hours && accent ? { backgroundColor: accent } : undefined}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-6 px-6 py-6">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <KpiCard label="Расход" value={fmtMoney(s.spend)} hint={`eCPM ${fmtMoney(s.ecpm)}`} />
          <KpiCard label="Показы" value={fmtNum(s.impressions)} />
          <KpiCard label="Клики" value={fmtNum(s.clicks)} hint={`CTR ${fmtPct(s.ctr)}`} />
          <KpiCard label="Win Rate" value={fmtPct(s.win_rate)} hint={`${fmtNum(s.wins)} из ${fmtNum(s.bids)}`} />
        </div>

        <Card>
          <CardHeader title="Показы по часам" />
          <div className="px-6 pb-4">
            <TimeseriesChart data={data.series} metrics={["impressions"]} granularity="hour" />
          </div>
        </Card>

        <Card>
          <CardHeader title="Кампании" />
          {data.campaigns.length > 0 ? (
            <Table headers={["Кампания", "Статус", "Показы", "Клики", "CTR", "Расход"]}>
              {data.campaigns.map((c) => (
                <tr key={c.id} className="hover:bg-surface">
                  <td className="px-4 py-3 font-medium text-foreground">{c.name}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={c.status} />
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtNum(c.impressions)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtNum(c.clicks)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtPct(c.ctr)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtMoney(c.spend)}</td>
                </tr>
              ))}
            </Table>
          ) : (
            <p className="px-4 pb-4 text-sm text-muted">Нет данных за выбранный период.</p>
          )}
        </Card>

        <p className="pb-4 text-center text-xs text-muted">Отчёт сформирован автоматически · {data.brand.name}</p>
      </main>
    </div>
  )
}
