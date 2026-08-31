"use client"

import { useState } from "react"
import useSWR from "swr"
import { fetcher } from "@/lib/api"
import { fmtMoney, fmtNum, fmtPct } from "@/lib/types"
import type { ScoringProfile, ScoringReport, ScoringRow } from "@/lib/types"
import { Card, CardHeader, EmptyState, HelpTip, PillTabs, Spinner } from "@/components/ui"

const REASON_LABELS: Record<string, string> = {
  low_ctr: "CTR ниже порога",
  low_viewability: "Viewability ниже порога",
  high_bounce: "Высокий отказ",
  low_depth: "Малая глубина",
  low_time: "Мало времени на сайте",
  high_cpa: "CPA выше цели",
}

type Dim = "domain" | "ssp"

function ScoreBadge({ score }: { score: number | null }) {
  if (score === null) {
    return <span className="rounded-full bg-panel px-2 py-0.5 font-mono text-xs text-muted">н/д</span>
  }
  const cls = score < 0.5 ? "bg-danger/10 text-danger" : score < 0.8 ? "bg-warning/10 text-warning" : "bg-success/10 text-success"
  return <span className={`inline-block rounded-full px-2 py-0.5 font-mono text-xs ${cls}`}>{score.toFixed(2)}</span>
}

/** Отчёт скоринга: агрегированные метрики качества по площадкам или SSP + итоговый score. */
export function ScoringReportCard({ profileId }: { profileId: string | null }) {
  const [dim, setDim] = useState<Dim>("domain")
  const qs = new URLSearchParams({ dimension: dim })
  if (profileId) qs.set("profile_id", profileId)
  const { data, isLoading } = useSWR<ScoringReport>(`/scoring/report?${qs.toString()}`, fetcher)

  const rows = data?.rows || []
  const profile = data?.profile
  const flagged = rows.filter((r) => r.flagged).length
  const hasBehavior = rows.some((r) => r.avg_depth !== null || r.avg_time !== null)

  return (
    <Card>
      <CardHeader
        title="Отчёт качества"
        subtitle={
          profile
            ? `Профиль «${profile.name}» · окно ${profile.window_days} дн · ${rows.length} строк · ${flagged} ниже порога`
            : "Скоринг площадок по правилам профиля"
        }
        action={
          <div className="flex items-center gap-2">
            <PillTabs<Dim>
              value={dim}
              onChange={setDim}
              options={[
                { key: "domain", label: "Площадки" },
                { key: "ssp", label: "SSP" },
              ]}
            />
            <HelpTip text="Каждая площадка/SSP оценивается по порогам профиля за окно исследования. Итоговый score = 1 − штрафы за нарушенные пороги. Профили в режиме «Блокировать» исключают площадки ниже порога из закупки." />
          </div>
        }
      />
      {isLoading ? (
        <Spinner />
      ) : rows.length === 0 ? (
        <EmptyState title="Нет данных за окно" description="Скоринг появится после накопления показов по площадкам." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                <th className="px-4 py-2.5 font-medium">{dim === "domain" ? "Площадка" : "SSP"}</th>
                <th className="px-4 py-2.5 text-right font-medium">Скоринг</th>
                <th className="px-4 py-2.5 text-right font-medium">Показы</th>
                <th className="px-4 py-2.5 text-right font-medium">CTR</th>
                <th className="px-4 py-2.5 text-right font-medium">Viewability</th>
                {hasBehavior ? <th className="px-4 py-2.5 text-right font-medium">Глубина</th> : null}
                {hasBehavior ? <th className="px-4 py-2.5 text-right font-medium">Время</th> : null}
                <th className="px-4 py-2.5 text-right font-medium">CPA</th>
                <th className="px-4 py-2.5 text-right font-medium">Расход</th>
                <th className="px-4 py-2.5 font-medium">Сигналы</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r: ScoringRow) => (
                <tr
                  key={r.domain || r.id}
                  className={`border-b border-border/50 ${r.flagged ? "bg-danger/[0.03]" : ""}`}
                >
                  <td className="whitespace-nowrap px-4 py-2.5 font-medium text-foreground">
                    {r.domain || r.name}
                  </td>
                  <td className="whitespace-nowrap px-4 py-2.5 text-right">
                    <ScoreBadge score={r.score} />
                  </td>
                  <td className="whitespace-nowrap px-4 py-2.5 text-right font-mono text-xs">{fmtNum(r.impressions)}</td>
                  <td className="whitespace-nowrap px-4 py-2.5 text-right font-mono text-xs">{fmtPct(r.ctr)}</td>
                  <td className="whitespace-nowrap px-4 py-2.5 text-right font-mono text-xs">{fmtPct(r.viewability)}</td>
                  {hasBehavior ? (
                    <td className="whitespace-nowrap px-4 py-2.5 text-right font-mono text-xs">
                      {r.avg_depth !== null ? r.avg_depth.toFixed(1) : "—"}
                    </td>
                  ) : null}
                  {hasBehavior ? (
                    <td className="whitespace-nowrap px-4 py-2.5 text-right font-mono text-xs">
                      {r.avg_time !== null ? `${Math.round(r.avg_time)}с` : "—"}
                    </td>
                  ) : null}
                  <td className="whitespace-nowrap px-4 py-2.5 text-right font-mono text-xs">
                    {r.cpa !== null ? fmtMoney(r.cpa) : "—"}
                  </td>
                  <td className="whitespace-nowrap px-4 py-2.5 text-right font-mono text-xs">{fmtMoney(r.spend)}</td>
                  <td className="px-4 py-2.5 text-xs text-muted">
                    {r.reasons.length ? (
                      <div className="flex flex-wrap gap-1">
                        {r.reasons.map((code) => (
                          <span key={code} className="rounded bg-danger/10 px-1.5 py-0.5 text-[10px] text-danger">
                            {REASON_LABELS[code] || code}
                          </span>
                        ))}
                      </div>
                    ) : (
                      "—"
                    )}
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
