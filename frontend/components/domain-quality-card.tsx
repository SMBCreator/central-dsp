"use client"

import useSWR from "swr"
import { fetcher } from "@/lib/api"
import { fmtNum } from "@/lib/types"
import { Card, CardHeader, EmptyState, HelpTip, Spinner } from "@/components/ui"

type DomainQualityRow = {
  domain: string
  score: number
  imps: number
  clicks: number
  wins: number
  reasons: string[]
  flagged: boolean
  updated_at: string | null
}

type DomainQualityResponse = {
  threshold: number
  domains: DomainQualityRow[]
}

const REASON_LABELS: Record<string, string> = {
  ctr_anomaly: "Аномальный CTR",
  click_burst: "Кликов больше показов",
  imp_no_win: "Показы без побед",
  zero_engage: "Нулевая вовлечённость",
}

/** Панель качества доменов (Часть B): скоринг ML-слоя по площадкам пользователя. */
export function DomainQualityCard() {
  const { data, isLoading } = useSWR<DomainQualityResponse>("/stats/domain-quality", fetcher)
  const rows = data?.domains || []
  const flagged = rows.filter((r) => r.flagged).length

  return (
    <Card>
      <CardHeader
        title="Качество площадок"
        subtitle={
          rows.length
            ? `${rows.length} доменов со скорингом · ${flagged} ниже порога ${data?.threshold ?? 0.5}`
            : "Скоринг доменов по сигналам скликивания и вовлечённости"
        }
        action={
          <HelpTip text="Скоринг пересчитывается ежедневно по всему трафику платформы: аномальный CTR, нулевая вовлечённость, победы без показов. Кампании с режимом «Только чистый трафик» не закупают домены ниже порога." />
        }
      />
      {isLoading ? (
        <div className="p-8">
          <Spinner />
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          title="Пока нет скоринга"
          description="Скоринг появится после накопления статистики показов по доменам."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                <th className="px-4 py-2.5 font-medium">Домен</th>
                <th className="px-4 py-2.5 text-right font-medium">Скоринг</th>
                <th className="px-4 py-2.5 text-right font-medium">Показы</th>
                <th className="px-4 py-2.5 text-right font-medium">Клики</th>
                <th className="px-4 py-2.5 font-medium">Сигналы</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.domain} className="border-b border-border/50">
                  <td className="whitespace-nowrap px-4 py-2.5 font-medium text-foreground">{r.domain}</td>
                  <td className="whitespace-nowrap px-4 py-2.5 text-right">
                    <span
                      className={`inline-block rounded-full px-2 py-0.5 font-mono text-xs ${
                        r.flagged ? "bg-danger/10 text-danger" : "bg-success/10 text-success"
                      }`}
                    >
                      {r.score.toFixed(2)}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-4 py-2.5 text-right font-mono text-xs">{fmtNum(r.imps)}</td>
                  <td className="whitespace-nowrap px-4 py-2.5 text-right font-mono text-xs">{fmtNum(r.clicks)}</td>
                  <td className="px-4 py-2.5 text-xs text-muted">
                    {r.reasons.length ? r.reasons.map((code) => REASON_LABELS[code] || code).join(", ") : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
