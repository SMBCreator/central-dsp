"use client"

import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import type { TimeseriesPoint } from "@/lib/types"

export function TimeseriesChart({
  data,
  metric,
  color = "var(--color-accent)",
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
      <AreaChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
        <defs>
          <linearGradient id={`grad-${metric}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.25} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" vertical={false} />
        <XAxis dataKey="time" tick={{ fontSize: 11, fill: "var(--color-muted)" }} tickLine={false} axisLine={false} />
        <YAxis tick={{ fontSize: 11, fill: "var(--color-muted)" }} tickLine={false} axisLine={false} />
        <Tooltip
          contentStyle={{
            background: "var(--color-card)",
            border: "1px solid var(--color-border)",
            borderRadius: 8,
            fontSize: 12,
            color: "var(--color-foreground)",
          }}
          formatter={(value: number) => [value, label]}
        />
        <Area type="monotone" dataKey={metric} stroke={color} strokeWidth={2} fill={`url(#grad-${metric})`} />
      </AreaChart>
    </ResponsiveContainer>
  )
}
