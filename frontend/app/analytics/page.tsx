"use client"

import { useState } from "react"
import useSWR from "swr"
import { api, fetcher } from "@/lib/api"
import {
  fmtMoney,
  fmtNum,
  fmtPct,
  type BreakdownRow,
  type StatsSummary,
  type TimeseriesPoint,
} from "@/lib/types"
import { Button, Card, CardHeader, ChartCard, EmptyState, KpiCard, PillTabs, Spinner, Table } from "@/components/ui"
import { TimeseriesChart } from "@/components/timeseries-chart"

type DomainRow = {
  domain: string
  wins: number
  impressions: number
  clicks: number
  spend: number
  blacklisted: boolean
}

const DIMENSIONS = [
  { key: "campaign", label: "По кампаниям" },
  { key: "partner", label: "По SSP" },
  { key: "creative", label: "По креативам" },
] as const

const PERIODS = [
  { key: "24", label: "Сутки" },
  { key: "168", label: "Неделя" },
  { key: "720", label: "Месяц" },
] as const

export default function AnalyticsPage() {
  const [hours, setHours] = useState(168)
  const [dim, setDim] = useState<(typeof DIMENSIONS)[number]["key"]>("campaign")

  const { data: summary } = useSWR<StatsSummary>(`/stats/summary?hours=${hours}`, fetcher)
  const { data: series } = useSWR<TimeseriesPoint[]>(`/stats/timeseries?hours=${hours}`, fetcher)
  const { data: breakdown } = useSWR<BreakdownRow[]>(`/stats/breakdown?hours=${hours}&dimension=${dim}`, fetcher)
  const { data: domains, mutate: mutateDomains } = useSWR<DomainRow[]>(`/stats/domains?hours=${hours}`, fetcher)
  const [blacklisting, setBlacklisting] = useState<string | null>(null)

  async function toBlacklist(domain: string) {
    setBlacklisting(domain)
    try {
      await api("/settings/domain-lists", {
        method: "POST",
        body: JSON.stringify({ domain, list_type: "black", source: "stats_click" }),
      })
      await mutateDomains()
    } finally {
      setBlacklisting(null)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Аналитика</h1>
          <p className="mt-0.5 text-sm text-muted">Детальные отчёты по закупке трафика</p>
        </div>
        <PillTabs
          value={String(hours) as (typeof PERIODS)[number]["key"]}
          onChange={(v) => setHours(Number(v))}
          options={PERIODS}
        />
      </div>

      {summary ? (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <KpiCard label="Расход" value={fmtMoney(summary.spend)} hint={`eCPM ${fmtMoney(summary.ecpm)}`} />
          <KpiCard label="Ставки → Выигрыши" value={`${fmtNum(summary.bids)} → ${fmtNum(summary.wins)}`} hint={`Win rate ${fmtPct(summary.win_rate)}`} />
          <KpiCard label="Показы" value={fmtNum(summary.impressions)} />
          <KpiCard label="Клики" value={fmtNum(summary.clicks)} hint={`CTR ${fmtPct(summary.ctr)}`} />
        </div>
      ) : (
        <Spinner />
      )}

      <ChartCard
        label="Расход по времени"
        badge="Spend"
        metric={summary ? fmtMoney(summary.spend) : undefined}
        metricUnit="суммарно за период"
        footerLeft={`${series?.length || 0} точек данных`}
        footerRight={summary ? `eCPM ${fmtMoney(summary.ecpm)}` : ""}
      >
        {series ? (
          series.length > 0 ? (
            <TimeseriesChart data={series} metric="spend" label="Расход" />
          ) : (
            <EmptyState title="Нет данных за выбранный период" />
          )
        ) : (
          <Spinner />
        )}
      </ChartCard>

      <Card>
        <CardHeader
          title="Разбивка"
          action={<PillTabs value={dim} onChange={setDim} options={DIMENSIONS} />}
        />
        {breakdown ? (
          breakdown.length > 0 ? (
            <Table headers={["Сегмент", "Ставки", "Выигрыши", "Показы", "Клики", "CTR", "Расход"]}>
              {breakdown.map((row) => (
                <tr key={row.id} className="hover:bg-surface">
                  <td className="px-4 py-3 font-medium text-foreground">{row.name}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtNum(row.bids)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtNum(row.wins)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtNum(row.impressions)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtNum(row.clicks)}</td>
                  <td className="px-4 py-3 font-mono text-xs">
                    {row.impressions > 0 ? fmtPct(row.ctr) : "—"}
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

      <Card>
        <CardHeader
          title="Площадки"
          subtitle="Домены и приложения, где выкупались показы. Клик — в чёрный список (глобально, все кампании)."
        />
        {domains ? (
          domains.length > 0 ? (
            <Table headers={["Домен", "Выигрыши", "Показы", "Клики", "Расход", ""]}>
              {domains.map((d) => (
                <tr key={d.domain} className="hover:bg-surface">
                  <td className="px-4 py-3 font-mono text-xs font-medium text-foreground">{d.domain}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtNum(d.wins)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtNum(d.impressions)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtNum(d.clicks)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtMoney(d.spend)}</td>
                  <td className="px-4 py-3 text-right">
                    {d.blacklisted ? (
                      <span className="text-xs text-muted">В чёрном списке</span>
                    ) : (
                      <Button
                        variant="secondary"
                        onClick={() => toBlacklist(d.domain)}
                        disabled={blacklisting === d.domain}
                      >
                        {blacklisting === d.domain ? "..." : "В блэклист"}
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </Table>
          ) : (
            <EmptyState title="Нет данных по площадкам" description="Статистика по доменам появится после первых выкупленных показов." />
          )
        ) : (
          <Spinner />
        )}
      </Card>
    </div>
  )
}
