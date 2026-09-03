"use client"

import { useEffect, useState } from "react"
import useSWR from "swr"
import { api, downloadCsv, fetcher } from "@/lib/api"
import { fmtMoney, fmtNum, fmtPct } from "@/lib/types"
import type { ScoringReport, ScoringRow, ScoringSortKey } from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, HelpTip, PillTabs, Spinner, inputCls } from "@/components/ui"

const REASON_LABELS: Record<string, string> = {
  low_ctr: "CTR ниже порога",
  low_viewability: "Viewability ниже порога",
  high_bounce: "Высокий отказ",
  low_depth: "Малая глубина",
  low_time: "Мало времени на сайте",
  high_cpa: "CPA выше цели",
}

type Dim = "domain" | "ssp"
type Order = "asc" | "desc"

const PAGE_SIZES = [25, 50, 100]

function ScoreBadge({ score }: { score: number | null }) {
  if (score === null) {
    return <span className="rounded-full bg-panel px-2 py-0.5 font-mono text-xs text-muted">н/д</span>
  }
  const cls = score < 0.5 ? "bg-danger/10 text-danger" : score < 0.8 ? "bg-warning/10 text-warning" : "bg-success/10 text-success"
  return <span className={`inline-block rounded-full px-2 py-0.5 font-mono text-xs ${cls}`}>{score.toFixed(2)}</span>
}

function BlockBadge({ row }: { row: ScoringRow }) {
  if (!row.blocked) return null
  const manual = row.block_source === "manual"
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] ${manual ? "bg-foreground/10 text-foreground" : "bg-danger/10 text-danger"}`}
      title={manual ? "Заблокирована вручную" : "Заблокирована профилем скоринга"}
    >
      {manual ? "ручной блок" : "блок"}
    </span>
  )
}

function SortTh({
  label,
  col,
  sort,
  order,
  onSort,
  align = "right",
}: {
  label: string
  col: ScoringSortKey
  sort: ScoringSortKey
  order: Order
  onSort: (col: ScoringSortKey) => void
  align?: "left" | "right"
}) {
  const active = sort === col
  return (
    <th
      className={`px-4 py-2.5 font-medium ${align === "right" ? "text-right" : ""}`}
      aria-sort={active ? (order === "asc" ? "ascending" : "descending") : "none"}
    >
      <button
        type="button"
        onClick={() => onSort(col)}
        className={`inline-flex items-center gap-1 uppercase tracking-wide hover:text-foreground ${active ? "text-foreground" : ""}`}
      >
        {label}
        <span aria-hidden className="w-2 font-mono text-[10px]">
          {active ? (order === "asc" ? "▲" : "▼") : ""}
        </span>
      </button>
    </th>
  )
}

/** Отчёт скоринга: серверная сортировка/пагинация/фильтры, ручные блокировки и экспорт CSV. */
export function ScoringReportCard({ profileId }: { profileId: string | null }) {
  const [dim, setDim] = useState<Dim>("domain")
  const [sort, setSort] = useState<ScoringSortKey>("score")
  const [order, setOrder] = useState<Order>("asc")
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [search, setSearch] = useState("")
  const [q, setQ] = useState("")
  const [onlyFlagged, setOnlyFlagged] = useState(false)
  const [onlyBlocked, setOnlyBlocked] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)

  // Debounce поиска: 300 мс, сброс на первую страницу.
  useEffect(() => {
    const t = setTimeout(() => {
      setQ(search.trim())
      setPage(1)
    }, 300)
    return () => clearTimeout(t)
  }, [search])

  const qs = new URLSearchParams({
    dimension: dim,
    sort,
    order,
    page: String(page),
    page_size: String(pageSize),
  })
  if (profileId) qs.set("profile_id", profileId)
  if (q) qs.set("q", q)
  if (onlyFlagged) qs.set("flagged", "1")
  if (onlyBlocked) qs.set("blocked", "1")
  const key = `/scoring/report?${qs.toString()}`
  const { data, isLoading, mutate } = useSWR<ScoringReport>(key, fetcher, { keepPreviousData: true })

  const rows = data?.rows || []
  const profile = data?.profile
  const total = data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const hasBehavior = rows.some((r) => r.avg_depth !== null || r.avg_time !== null)

  function onSort(col: ScoringSortKey) {
    if (sort === col) setOrder(order === "asc" ? "desc" : "asc")
    else {
      setSort(col)
      setOrder(col === "domain" || col === "name" ? "asc" : "desc")
    }
    setPage(1)
  }

  function switchDim(d: Dim) {
    setDim(d)
    setSort("score")
    setOrder("asc")
    setPage(1)
    setOnlyBlocked(false)
  }

  async function toggleBlock(row: ScoringRow) {
    if (!row.domain) return
    setBusy(row.domain)
    try {
      if (row.blocked && row.block_source === "manual") {
        await api(`/scoring/blocks/${encodeURIComponent(row.domain)}`, { method: "DELETE" })
      } else if (!row.blocked) {
        await api("/scoring/blocks", { method: "POST", body: { domain: row.domain } })
      }
      await mutate()
    } finally {
      setBusy(null)
    }
  }

  async function exportCsv() {
    setExporting(true)
    try {
      const p = new URLSearchParams(qs)
      p.delete("page")
      p.delete("page_size")
      await downloadCsv(`/scoring/report.csv?${p.toString()}`, `scoring-${dim}.csv`)
    } finally {
      setExporting(false)
    }
  }

  const subtitle = profile
    ? `Профиль «${profile.name}» · окно ${data?.window_days ?? profile.window_days} дн · ${fmtNum(total)} строк · ${fmtNum(
        data?.total_flagged ?? 0,
      )} ниже порога${dim === "domain" ? ` · ${fmtNum(data?.total_blocked ?? 0)} в блоке` : ""}`
    : "Скоринг площадок по правилам профиля"

  return (
    <Card>
      <CardHeader
        title="Отчёт качества"
        subtitle={subtitle}
        action={
          <div className="flex items-center gap-2">
            <PillTabs<Dim>
              value={dim}
              onChange={switchDim}
              options={[
                { key: "domain", label: "Площадки" },
                { key: "ssp", label: "SSP" },
              ]}
            />
            <Button variant="secondary" onClick={exportCsv} disabled={exporting || total === 0}>
              {exporting ? "Экспорт…" : "CSV"}
            </Button>
            <HelpTip text="Каждая площадка/SSP оценивается по порогам профиля за окно исследования. Итоговый score = 1 − штрафы за нарушенные пороги. Профили в режиме «Блокировать» исключают площадки ниже порога из закупки. Ручные блоки не снимаются автопересчётом." />
          </div>
        }
      />

      <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={dim === "domain" ? "Поиск по домену" : "Поиск по SSP"}
          aria-label="Поиск"
          className={`${inputCls} h-8 w-56`}
        />
        <label className="flex items-center gap-2 text-sm text-muted">
          <input
            type="checkbox"
            checked={onlyFlagged}
            onChange={(e) => {
              setOnlyFlagged(e.target.checked)
              setPage(1)
            }}
          />
          Только ниже порога
        </label>
        {dim === "domain" ? (
          <label className="flex items-center gap-2 text-sm text-muted">
            <input
              type="checkbox"
              checked={onlyBlocked}
              onChange={(e) => {
                setOnlyBlocked(e.target.checked)
                setPage(1)
              }}
            />
            Только в блоке
          </label>
        ) : null}
      </div>

      {isLoading && !data ? (
        <Spinner />
      ) : rows.length === 0 ? (
        <EmptyState
          title={q || onlyFlagged || onlyBlocked ? "Ничего не найдено" : "Нет данных за окно"}
          description={
            q || onlyFlagged || onlyBlocked
              ? "Измените фильтры или поисковый запрос."
              : "Скоринг появится после накопления показов по площадкам."
          }
        />
      ) : (
        <>
          <div className={`overflow-x-auto ${isLoading ? "opacity-60" : ""}`}>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                  <SortTh label={dim === "domain" ? "Площадка" : "SSP"} col={dim === "domain" ? "domain" : "name"} sort={sort} order={order} onSort={onSort} align="left" />
                  <SortTh label="Скоринг" col="score" sort={sort} order={order} onSort={onSort} />
                  <SortTh label="Показы" col="impressions" sort={sort} order={order} onSort={onSort} />
                  <SortTh label="CTR" col="ctr" sort={sort} order={order} onSort={onSort} />
                  <SortTh label="Viewability" col="viewability" sort={sort} order={order} onSort={onSort} />
                  {hasBehavior ? <SortTh label="Глубина" col="avg_depth" sort={sort} order={order} onSort={onSort} /> : null}
                  {hasBehavior ? <SortTh label="Время" col="avg_time" sort={sort} order={order} onSort={onSort} /> : null}
                  <SortTh label="CPA" col="cpa" sort={sort} order={order} onSort={onSort} />
                  <SortTh label="Расход" col="spend" sort={sort} order={order} onSort={onSort} />
                  <th className="px-4 py-2.5 font-medium">Сигналы</th>
                  {dim === "domain" ? <th className="px-4 py-2.5 font-medium" /> : null}
                </tr>
              </thead>
              <tbody>
                {rows.map((r: ScoringRow) => {
                  const manual = r.blocked && r.block_source === "manual"
                  const canToggle = dim === "domain" && (!r.blocked || manual)
                  return (
                    <tr
                      key={r.domain || r.id}
                      className={`border-b border-border/50 ${r.blocked ? "bg-danger/[0.05]" : r.flagged ? "bg-danger/[0.03]" : ""}`}
                    >
                      <td className="whitespace-nowrap px-4 py-2.5 font-medium text-foreground">
                        <div className="flex items-center gap-2">
                          <span>{r.domain || r.name}</span>
                          <BlockBadge row={r} />
                        </div>
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
                      {dim === "domain" ? (
                        <td className="whitespace-nowrap px-4 py-2.5 text-right">
                          {canToggle ? (
                            <Button
                              variant={r.blocked ? "secondary" : "danger"}
                              onClick={() => toggleBlock(r)}
                              disabled={busy === r.domain}
                              className="h-7 px-2 text-xs"
                            >
                              {r.blocked ? "Разблокировать" : "Заблокировать"}
                            </Button>
                          ) : (
                            <span className="text-[11px] text-muted" title="Снимается только изменением профиля или пересчётом">
                              авто
                            </span>
                          )}
                        </td>
                      ) : null}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm text-muted">
            <div className="flex items-center gap-2">
              <span>
                {fmtNum((page - 1) * pageSize + 1)}–{fmtNum(Math.min(page * pageSize, total))} из {fmtNum(total)}
              </span>
              <select
                value={pageSize}
                onChange={(e) => {
                  setPageSize(Number(e.target.value))
                  setPage(1)
                }}
                aria-label="Строк на странице"
                className={`${inputCls} h-7 w-auto py-0 text-xs`}
              >
                {PAGE_SIZES.map((n) => (
                  <option key={n} value={n}>
                    {n} / стр
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-1">
              <Button variant="ghost" onClick={() => setPage(1)} disabled={page <= 1} className="h-7 px-2 text-xs">
                «
              </Button>
              <Button variant="ghost" onClick={() => setPage(page - 1)} disabled={page <= 1} className="h-7 px-2 text-xs">
                Назад
              </Button>
              <span className="px-2 font-mono text-xs">
                {page} / {pages}
              </span>
              <Button variant="ghost" onClick={() => setPage(page + 1)} disabled={page >= pages} className="h-7 px-2 text-xs">
                Вперёд
              </Button>
              <Button variant="ghost" onClick={() => setPage(pages)} disabled={page >= pages} className="h-7 px-2 text-xs">
                »
              </Button>
            </div>
          </div>
        </>
      )}
    </Card>
  )
}
