"use client"

import { useMemo } from "react"
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"

export type MetricKey = string

/**
 * Цвета метрик: показы — акцентный, клики — зелёный, расход — янтарный,
 * ставки — нейтральный серый. Токены из globals.css, работают в обеих темах.
 */
export const METRIC_COLORS: Record<string, string> = {
  impressions: "var(--color-accent)",
  clicks: "var(--color-success)",
  spend: "var(--color-warning)",
  bids: "var(--chart-2)",
  wins: "var(--chart-1)",
  ctr: "var(--chart-3)",
}

const METRIC_LABELS: Record<string, string> = {
  impressions: "Показы",
  clicks: "Клики",
  spend: "Расход",
  bids: "Ставки",
  wins: "Выигрыши",
  ctr: "CTR",
}

/** Метрики в деньгах (₽) и в процентах — для форматирования осей и тултипа. */
const MONEY_METRICS = new Set(["spend", "cpm", "cpc", "cpa"])
const PCT_METRICS = new Set(["ctr", "win_rate", "viewability"])

/**
 * Бэкенд агрегирует бакеты по UTC (date_trunc на ts_hour/created_at), поэтому и
 * подписи рисуем в UTC — иначе в браузере с МСК суточный бакет «уезжал» бы на 03:00,
 * а в поясах западнее UTC дневные бакеты показывали бы предыдущую дату.
 */
export const CHART_TZ = "UTC" as const
export const TZ_NOTE = "Время — UTC (МСК = UTC+3)"

const fmtDM = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", timeZone: CHART_TZ })
const fmtHM = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: CHART_TZ })
const fmtDMonthHM = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "long",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: CHART_TZ,
})
const fmtDMonthY = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "long", year: "numeric", timeZone: CHART_TZ })

function formatTick(ts: string, granularity: "hour" | "day" | "week"): string {
  const d = new Date(ts)
  if (isNaN(d.getTime())) return ts
  if (granularity === "hour") {
    // Часы дня без даты — дата видна в тултипе; при переходе суток (00:00 UTC) покажем дату
    return d.getUTCHours() === 0 ? fmtDM.format(d) : fmtHM.format(d)
  }
  return fmtDM.format(d)
}

function formatTooltipTs(ts: string, granularity: "hour" | "day" | "week"): string {
  const d = new Date(ts)
  if (isNaN(d.getTime())) return ts
  if (granularity === "hour") return `${fmtDMonthHM.format(d)} UTC`
  if (granularity === "week") {
    const end = new Date(d.getTime() + 6 * 864e5)
    return `Неделя ${fmtDM.format(d)} – ${fmtDM.format(end)}`
  }
  return fmtDMonthY.format(d)
}

const nfRu = new Intl.NumberFormat("ru-RU")
const nfCompact = new Intl.NumberFormat("ru-RU", { notation: "compact", maximumFractionDigits: 1 })

function fmtAxisValue(metric: string, v: number): string {
  if (MONEY_METRICS.has(metric)) return `${nfCompact.format(v)} ₽`
  if (PCT_METRICS.has(metric)) return `${nfCompact.format(v)}%`
  return nfCompact.format(v)
}

function fmtTooltipValue(metric: string, v: number): string {
  if (MONEY_METRICS.has(metric)) return `${nfRu.format(Math.round(v * 100) / 100)} ₽`
  if (PCT_METRICS.has(metric)) return `${nfRu.format(Math.round(v * 1000) / 1000)}%`
  return nfRu.format(v)
}

type AxisSlot = { orientation: "left" | "right"; hide: boolean }

/**
 * Раскладка осей как в Google Ads: каждая метрика живёт на СВОЕЙ оси
 * (собственный масштаб — линии всегда читабельны), но видимых осей максимум две.
 * Правила:
 *  - клики — всегда левая ось, показы — всегда правая;
 *  - если кликов/показов нет, свободные стороны занимают остальные метрики по порядку;
 *  - все прочие метрики (расход, ставки, CTR) рисуются линией и видны в тултипе,
 *    но собственную шкалу не показывают — оси не конфликтуют.
 */
function buildAxisPlan(metrics: string[]): Map<string, AxisSlot> {
  const plan = new Map<string, AxisSlot>()
  const used = new Set<"left" | "right">()

  if (metrics.includes("clicks")) {
    plan.set("clicks", { orientation: "left", hide: false })
    used.add("left")
  }
  if (metrics.includes("impressions")) {
    plan.set("impressions", { orientation: "right", hide: false })
    used.add("right")
  }
  for (const m of metrics) {
    if (plan.has(m)) continue
    if (used.size >= 2) {
      plan.set(m, { orientation: "left", hide: true })
      continue
    }
    const side: "left" | "right" = used.has("left") ? "right" : "left"
    plan.set(m, { orientation: side, hide: false })
    used.add(side)
  }
  return plan
}

/**
 * График с поддержкой нескольких метрик одновременно.
 * Оси распределяются по buildAxisPlan: клики слева, показы справа,
 * деньги и проценты — на скрытых осях (значения видны при наведении).
 */
export function TimeseriesChart<T extends { ts: string }>({
  data,
  metrics,
  granularity = "hour",
}: {
  data: T[]
  metrics: MetricKey[]
  granularity?: "hour" | "day" | "week"
}) {
  const axisPlan = useMemo(() => buildAxisPlan(metrics), [metrics])
  const rightVisible = Array.from(axisPlan.values()).some((a) => !a.hide && a.orientation === "right")

  return (
    <ResponsiveContainer width="100%" height={260}>
      <AreaChart data={data} margin={{ top: 8, right: rightVisible ? 8 : 16, bottom: 0, left: 0 }}>
        <defs>
          {metrics.map((m) => (
            <linearGradient key={m} id={`grad-${m}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={METRIC_COLORS[m]} stopOpacity={0.14} />
              <stop offset="100%" stopColor={METRIC_COLORS[m]} stopOpacity={0} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid strokeDasharray="2 4" stroke="var(--color-border)" vertical={false} />
        <XAxis
          dataKey="ts"
          interval="preserveStartEnd"
          tickFormatter={(ts: string) => formatTick(ts, granularity)}
          tick={{ fontSize: 10, fill: "var(--color-muted)", fontFamily: "var(--font-mono)" }}
          tickLine={false}
          axisLine={false}
          minTickGap={32}
        />
        {metrics.map((m) => {
          const slot = axisPlan.get(m)
          if (!slot) return null
          return (
            <YAxis
              key={`axis-${m}`}
              yAxisId={m}
              orientation={slot.orientation}
              hide={slot.hide}
              tickFormatter={(v: number) => fmtAxisValue(m, v)}
              tick={{ fontSize: 10, fill: METRIC_COLORS[m] || "var(--color-muted)", fontFamily: "var(--font-mono)" }}
              tickLine={false}
              axisLine={false}
              width={slot.hide ? 0 : 62}
            />
          )
        })}
        <Tooltip
          labelFormatter={(ts: string) => formatTooltipTs(ts, granularity)}
          contentStyle={{
            background: "var(--color-panel)",
            border: "1px solid var(--color-border)",
            borderRadius: 12,
            fontSize: 12,
            fontFamily: "var(--font-mono)",
            color: "var(--color-foreground)",
          }}
          formatter={(value: number, name: string) => [fmtTooltipValue(name, value), METRIC_LABELS[name] || name]}
        />
        {metrics.map((m) => (
          <Area
            key={m}
            yAxisId={m}
            type="monotone"
            dataKey={m}
            stroke={METRIC_COLORS[m]}
            strokeWidth={2}
            strokeLinecap="round"
            fill={metrics.length === 1 ? `url(#grad-${m})` : "transparent"}
            dot={false}
            activeDot={{ r: 3.5, fill: METRIC_COLORS[m], stroke: "var(--color-card)", strokeWidth: 2 }}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  )
}
