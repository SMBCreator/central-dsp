"use client"

import { useState } from "react"
import useSWR from "swr"
import { fetcher } from "@/lib/api"
import { fmtMoney, fmtNum, fmtPct } from "@/lib/types"
import { Card, CardHeader, KpiCard, Spinner, StatusBadge, Table } from "@/components/ui"
import { TimeseriesChart } from "@/components/timeseries-chart"

interface MyScope {
  is_guest: boolean
  advertiser_id: string | null
  advertiser_name: string | null
  brand_name: string | null
  brand_color: string | null
  brand_logo_url: string | null
}

interface Summary {
  bids: number
  wins: number
  impressions: number
  clicks: number
  spend: number
  win_rate: number
  ctr: number
  ecpm: number
}

interface SeriesPoint {
  ts: string
  bids: number
  wins: number
  impressions: number
  clicks: number
  spend: number
}

interface CampaignRow {
  id: string
  name: string
  status: string
  impressions: number
  clicks: number
  spend: number
  ctr: number
}

const PERIODS = [
  { hours: 24, label: "24 часа" },
  { hours: 168, label: "7 дней" },
  { hours: 720, label: "30 дней" },
]

/**
 * Отчёт для роли viewer (гостевой аккаунт): брендированная сводка
 * по scope из guest_access. Данные скоупятся на сервере (_resolve_scope).
 */
export default function ViewerReportPage() {
  const [hours, setHours] = useState(168)
  const { data: scope, error: scopeError } = useSWR<MyScope>("/share/my-scope", fetcher)
  const { data: summary } = useSWR<Summary>(`/stats/summary?hours=${hours}`, fetcher)
  const { data: series } = useSWR<SeriesPoint[]>(`/stats/timeseries?hours=${hours}`, fetcher)
  const { data: campaigns } = useSWR<CampaignRow[]>(`/stats/breakdown?hours=${hours}&dimension=campaign`, fetcher)

  if (scopeError) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center p-6">
        <div className="text-center">
          <p className="text-lg font-semibold text-foreground">Отчёт недоступен</p>
          <p className="mt-1 text-sm text-muted">
            {scopeError instanceof Error ? scopeError.message : "Гостевой доступ не настроен — обратитесь к владельцу аккаунта"}
          </p>
        </div>
      </div>
    )
  }
  if (!scope || !summary) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <Spinner />
      </div>
    )
  }

  const brandName = scope.brand_name || "Отчёт по рекламе"
  const accent = scope.brand_color || undefined
  const s = summary

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          {scope.brand_logo_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={scope.brand_logo_url} alt={brandName} className="h-10 w-10 rounded-full object-cover" />
          ) : (
            <div
              className="flex h-10 w-10 items-center justify-center rounded-full bg-primary"
              style={accent ? { backgroundColor: accent } : undefined}
            >
              <span className="text-sm font-bold text-primary-foreground">{brandName.charAt(0)}</span>
            </div>
          )}
          <div>
            <h1 className="text-lg font-semibold text-foreground">{brandName}</h1>
            <p className="text-xs text-muted">
              {scope.advertiser_name ? `Рекламодатель: ${scope.advertiser_name}` : "Все кампании аккаунта"}
            </p>
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
      </header>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard label="Расход" value={fmtMoney(s.spend)} hint={`eCPM ${fmtMoney(s.ecpm)}`} />
        <KpiCard label="Показы" value={fmtNum(s.impressions)} />
        <KpiCard label="Клики" value={fmtNum(s.clicks)} hint={`CTR ${fmtPct(s.ctr)}`} />
        <KpiCard label="Win Rate" value={fmtPct(s.win_rate)} hint={`${fmtNum(s.wins)} из ${fmtNum(s.bids)}`} />
      </div>

      <Card>
        <CardHeader title="Показы по часам" />
        <div className="px-4 pb-4">
          {series && series.length > 0 ? (
            <TimeseriesChart data={series} metrics={["impressions"]} granularity="hour" />
          ) : (
            <p className="text-sm text-muted">Нет данных за выбранный период.</p>
          )}
        </div>
      </Card>

      <Card>
        <CardHeader title="Кампании" />
        {campaigns && campaigns.length > 0 ? (
          <Table headers={["Кампания", "Статус", "Показы", "Клики", "CTR", "Расход"]}>
            {campaigns.map((c) => (
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

      <p className="pb-4 text-center text-xs text-muted">Отчёт сформирован автоматически · {brandName}</p>
    </div>
  )
}
