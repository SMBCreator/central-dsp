"use client"

import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import type { TimeseriesPoint } from "@/lib/types"

export type MetricKey = keyof Omit<TimeseriesPoint, "ts">

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
}

const METRIC_LABELS: Record<string, string> = {
  impressions: "Показы",
  clicks: "Клики",
  spend: "Расход",
  bids: "Ставки",
  wins: "Выигрыши",
}

function formatTick(ts: string, granularity: "hour" | "day" | "week"): string {
  const d = new Date(ts)
  if (granularity === "hour") {
    // Часы дня без даты — дата видна в тултипе; при переходе суток покажем дату
    return d.getHours() === 0
      ? d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" })
      : d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })
  }
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" })
}

function formatTooltipTs(ts: string, granularity: "hour" | "day" | "week"): string {
  const d = new Date(ts)
  if (granularity === "hour")
    return d.toLocaleString("ru-RU", { day: "2-digit", month: "long", hour: "2-digit", minute: "2-digit" })
  if (granularity === "week") {
    const end = new Date(d.getTime() + 6 * 864e5)
    return `Неделя ${d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" })} – ${end.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" })}`
  }
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "long", year: "numeric" })
}

const nfRu = new Intl.NumberFormat("ru-RU")
const nfCompact = new Intl.NumberFormat("ru-RU", { notation: "compact", maximumFractionDigits: 1 })

/**
 * График с поддержкой нескольких метрик одновременно.
 * Расход (деньги) идёт на правую ось Y, счётчики — на левую,
 * чтобы линии с разными порядками величин были читабельны.
 */
export function TimeseriesChart({
  data,
  metrics,
  granularity = "hour",
}: {
  data: TimeseriesPoint[]
  metrics: MetricKey[]
  granularity?: "hour" | "day" | "week"
}) {
  const hasSpend = metrics.includes("spend")
  const hasCounts = metrics.some((m) => m !== "spend")

  // Прореживание подписей X: не больше ~8 подписей независимо от числа точек
  const tickInterval = Math.max(0, Math.ceil(data.length / 8) - 1)

  return (
    <ResponsiveContainer width="100%" height={260}>
      <AreaChart data={data} margin={{ top: 8, right: hasSpend ? 8 : 16, bottom: 0, left: 0 }}>
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
          interval={tickInterval}
          tickFormatter={(ts: string) => formatTick(ts, granularity)}
          tick={{ fontSize: 10, fill: "var(--color-muted)", fontFamily: "var(--font-mono)" }}
          tickLine={false}
          axisLine={false}
          minTickGap={24}
        />
        {hasCounts ? (
          <YAxis
            yAxisId="counts"
            tickFormatter={(v: number) => nfCompact.format(v)}
            tick={{ fontSize: 10, fill: "var(--color-muted)", fontFamily: "var(--font-mono)" }}
            tickLine={false}
            axisLine={false}
            width={62}
          />
        ) : null}
        {hasSpend ? (
          <YAxis
            yAxisId="money"
            orientation={hasCounts ? "right" : "left"}
            tickFormatter={(v: number) => `${nfCompact.format(v)} ₽`}
            tick={{ fontSize: 10, fill: "var(--color-warning)", fontFamily: "var(--font-mono)" }}
            tickLine={false}
            axisLine={false}
            width={56}
          />
        ) : null}
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
          formatter={(value: number, name: string) => [
            name === "spend" ? `${nfRu.format(Math.round(value * 100) / 100)} ₽` : nfRu.format(value),
            METRIC_LABELS[name] || name,
          ]}
        />
        {metrics.map((m) => (
          <Area
            key={m}
            yAxisId={m === "spend" ? "money" : "counts"}
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
