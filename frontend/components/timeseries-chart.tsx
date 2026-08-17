"use client"

import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import type { TimeseriesPoint } from "@/lib/types"

/**
 * Monochrome area chart: foreground-colored spline with a soft
 * fade fill, dashed grid, monospace axis ticks.
 */
export function TimeseriesChart({
  data,
  metric,
  color = "var(--chart-1)",
  label,
}: {
  data: TimeseriesPoint[]
  metric: keyof Omit<TimeseriesPoint, "ts">
  color?: string
  label: string
}) {
  const chartData = data.map((d) => ({
    ...d,
    time: new Date(d.ts).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit" }),
  }))

  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
        <defs>
          <linearGradient id={`grad-${metric}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.18} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="2 4" stroke="var(--color-border)" vertical={false} />
        <XAxis
          dataKey="time"
          tick={{ fontSize: 10, fill: "var(--color-muted)", fontFamily: "var(--font-mono)" }}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          tick={{ fontSize: 10, fill: "var(--color-muted)", fontFamily: "var(--font-mono)" }}
          tickLine={false}
          axisLine={false}
        />
        <Tooltip
          contentStyle={{
            background: "var(--color-panel)",
            border: "1px solid var(--color-border)",
            borderRadius: 12,
            fontSize: 12,
            fontFamily: "var(--font-mono)",
            color: "var(--color-foreground)",
          }}
          formatter={(value: number) => [value, label]}
        />
        <Area
          type="monotone"
          dataKey={metric}
          stroke={color}
          strokeWidth={2}
          strokeLinecap="round"
          fill={`url(#grad-${metric})`}
          dot={false}
          activeDot={{ r: 3.5, fill: color, stroke: "var(--color-card)", strokeWidth: 2 }}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
