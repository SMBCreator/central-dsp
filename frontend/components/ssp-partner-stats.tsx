"use client"

import { useState } from "react"
import useSWR from "swr"
import { fetcher } from "@/lib/api"
import { AD_TYPE_LABELS, fmtMoney, fmtNum, fmtPct, type SupplyPartner, type SupplyPartnerStats } from "@/lib/types"
import { HelpTip, PillTabs, Spinner, Table } from "@/components/ui"
import { TZ_NOTE, TimeseriesChart } from "@/components/timeseries-chart"

type Window = "1" | "7" | "30"

const WINDOWS: readonly { key: Window; label: string }[] = [
  { key: "1", label: "24 часа" },
  { key: "7", label: "7 дней" },
  { key: "30", label: "30 дней" },
]

const pct = (v: number | null) => (v === null ? "—" : fmtPct(v))
const money = (v: number | null, currency: string) => (v === null ? "—" : fmtMoney(v, currency))

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg bg-panel px-4 py-3">
      <p className="flex items-center gap-1 font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-muted">
        {label}
        {hint ? <HelpTip text={hint} /> : null}
      </p>
      <p className="mt-1 text-lg font-semibold tracking-tight text-foreground">{value}</p>
    </div>
  )
}

export function SspPartnerStats({ partner }: { partner: SupplyPartner }) {
  const [win, setWin] = useState<Window>("7")
  const currency = partner.currency || "RUB"
  const { data, isLoading, error } = useSWR<SupplyPartnerStats>(
    `/supply-partners/${partner.id}/stats?days=${win}`,
    fetcher,
  )

  return (
    <div className="flex flex-col gap-4 border-t border-border px-6 py-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm text-muted">
          <span>Торговля с партнёром</span>
          <HelpTip text="Считается по нашим событиям в bid_events: ставки, выигрыши, показы, клики, loss-нотификации. Число входящих запросов пишется только в Prometheus (bidder_partner_requests_total), поэтому fill-rate смотрите в Grafana." />
        </div>
        <PillTabs value={win} onChange={setWin} options={WINDOWS} />
      </div>

      {error ? (
        <p role="alert" className="text-sm text-danger">
          Не удалось загрузить статистику партнёра.
        </p>
      ) : isLoading || !data ? (
        <div className="flex items-center gap-2 py-6 text-sm text-muted">
          <Spinner /> Загружаем…
        </div>
      ) : data.totals.bids === 0 && data.totals.losses === 0 ? (
        <p className="py-4 text-sm text-muted">
          За выбранный период событий по этому партнёру нет. Если партнёр активен — проверьте, что SSP шлёт запросы
          на эндпоинт и что в фиде есть подходящие кампании.
        </p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Metric label="Ставки" value={fmtNum(data.totals.bids)} />
            <Metric
              label="Win rate"
              value={pct(data.totals.win_rate)}
              hint="Выигрыши / ставки. Ниже 5% при большом объёме — смотрите причины проигрышей и bidfloor."
            />
            <Metric label="Показы" value={fmtNum(data.totals.impressions)} />
            <Metric label="Расход" value={money(data.totals.spend, currency)} />
            <Metric label="CTR" value={pct(data.totals.ctr)} />
            <Metric
              label="Средняя ставка"
              value={money(data.totals.avg_bid, currency)}
              hint="Средний CPM наших ставок партнёру."
            />
            <Metric
              label="Средний bidfloor"
              value={money(data.totals.avg_bidfloor, currency)}
              hint="Средний минимум, который SSP ставит в запросе. Если выше нашей средней ставки — мы почти не выигрываем."
            />
            <Metric
              label="Мин. цена выигрыша"
              value={money(data.totals.avg_min_to_win, currency)}
              hint="Среднее min_to_win из loss-нотификаций: сколько нужно было поставить, чтобы выиграть."
            />
          </div>

          {data.daily.length > 1 ? (
            <div className="rounded-lg bg-panel p-3">
              <TimeseriesChart data={data.daily} metrics={["bids", "wins"]} granularity="day" />
              <p className="mt-2 font-mono text-[11px] text-muted">{TZ_NOTE}</p>
            </div>
          ) : null}

          <div className="grid gap-4 lg:grid-cols-2">
            <section aria-labelledby={`loss-${partner.id}`}>
              <h4 id={`loss-${partner.id}`} className="mb-2 text-sm font-medium text-foreground">
                Причины проигрышей
              </h4>
              {data.loss_reasons.length === 0 ? (
                <p className="text-sm text-muted">
                  Loss-нотификаций нет. Уточните у SSP, шлют ли они `lurl` — без них не видно, почему мы проигрываем.
                </p>
              ) : (
                <ul className="flex flex-col gap-1.5">
                  {data.loss_reasons.map((r) => (
                    <li key={r.reason} className="flex items-center gap-3 text-sm">
                      <span className="w-10 shrink-0 font-mono text-xs text-muted">{r.reason}</span>
                      <span className="min-w-0 flex-1 truncate text-foreground">{r.label}</span>
                      <span className="h-1.5 w-24 shrink-0 overflow-hidden rounded bg-border">
                        <span
                          className="block h-full bg-accent"
                          style={{ width: `${Math.max(2, r.share ?? 0)}%` }}
                          aria-hidden
                        />
                      </span>
                      <span className="w-20 shrink-0 text-right font-mono text-xs text-muted">
                        {fmtNum(r.count)} · {pct(r.share)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section aria-labelledby={`types-${partner.id}`}>
              <h4 id={`types-${partner.id}`} className="mb-2 text-sm font-medium text-foreground">
                Форматы
              </h4>
              <ul className="flex flex-col gap-1.5">
                {data.ad_types.map((t) => (
                  <li key={t.ad_type} className="flex items-center justify-between gap-3 text-sm">
                    <span className="text-foreground">{AD_TYPE_LABELS[t.ad_type] || t.ad_type}</span>
                    <span className="font-mono text-xs text-muted">
                      {fmtNum(t.bids)} ставок · win {pct(t.win_rate)}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          </div>

          <section aria-labelledby={`pl-${partner.id}`}>
            <h4 id={`pl-${partner.id}`} className="mb-2 text-sm font-medium text-foreground">
              Топ площадок по ставкам
            </h4>
            <div className="rounded-lg bg-panel">
              <Table headers={["Publisher", "tagid", "Сайт / bundle", "Ставки", "Win rate", "Показы", "Расход"]}>
                {data.placements.map((p) => (
                  <tr key={`${p.publisher_id}-${p.tagid}-${p.site}`} className="border-b border-border/60 last:border-0">
                    <td className="px-4 py-2 font-mono text-xs">{p.publisher_id}</td>
                    <td className="px-4 py-2 font-mono text-xs">{p.tagid}</td>
                    <td className="max-w-[16rem] truncate px-4 py-2">{p.site}</td>
                    <td className="px-4 py-2 tabular-nums">{fmtNum(p.bids)}</td>
                    <td className="px-4 py-2 tabular-nums">{pct(p.win_rate)}</td>
                    <td className="px-4 py-2 tabular-nums">{fmtNum(p.impressions)}</td>
                    <td className="px-4 py-2 tabular-nums">{money(p.spend, currency)}</td>
                  </tr>
                ))}
              </Table>
            </div>
          </section>
        </>
      )}
    </div>
  )
}
