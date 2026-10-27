"use client"

import { useState } from "react"
import useSWR from "swr"
import { fetcher } from "@/lib/api"
import type { BreakdownRow, StatsSummary } from "@/lib/types"
import { Button, Card, CardHeader, Field, inputCls, Spinner } from "@/components/ui"

interface CreativeVariant {
  title: string
  description: string
  cta: string
}

interface Insight {
  severity: "info" | "warning" | "critical"
  title: string
  recommendation: string
}

export default function AiToolsPage() {
  const { data: summary } = useSWR<StatsSummary>("/stats/summary?hours=168", fetcher)
  const { data: breakdown } = useSWR<BreakdownRow[]>("/stats/breakdown?hours=168&dimension=campaign", fetcher)

  const [product, setProduct] = useState("")
  const [audience, setAudience] = useState("")
  const [tone, setTone] = useState("нейтральная")
  const [variants, setVariants] = useState<CreativeVariant[] | null>(null)
  const [genLoading, setGenLoading] = useState(false)
  const [genError, setGenError] = useState<string | null>(null)

  const [insights, setInsights] = useState<Insight[] | null>(null)
  const [insLoading, setInsLoading] = useState(false)
  const [insError, setInsError] = useState<string | null>(null)

  const generate = async (e: React.FormEvent) => {
    e.preventDefault()
    setGenLoading(true)
    setGenError(null)
    setVariants(null)
    try {
      const res = await fetch("/ai-api/creative", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ product, audience, tone }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || "Ошибка генерации")
      setVariants(data.variants)
    } catch (err) {
      setGenError(err instanceof Error ? err.message : "Ошибка генерации")
    } finally {
      setGenLoading(false)
    }
  }

  const analyze = async () => {
    setInsLoading(true)
    setInsError(null)
    setInsights(null)
    try {
      const res = await fetch("/ai-api/insights", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stats: { summary, campaigns: breakdown } }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || "Ошибка анализа")
      setInsights(data.insights)
    } catch (err) {
      setInsError(err instanceof Error ? err.message : "Ошибка анализа")
    } finally {
      setInsLoading(false)
    }
  }

  const severityStyle: Record<Insight["severity"], string> = {
    info: "border-accent/30 bg-accent/5",
    warning: "border-warning/30 bg-warning/5",
    critical: "border-danger/30 bg-danger/5",
  }
  const severityLabel: Record<Insight["severity"], string> = {
    info: "Наблюдение",
    warning: "Внимание",
    critical: "Критично",
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">AI-инструменты</h1>
        <p className="mt-0.5 text-sm text-muted">Генерация текстов объявлений и AI-анализ эффективности</p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Генератор текстов объявлений" subtitle="3 варианта заголовков и описаний" />
          <form onSubmit={generate} className="space-y-4 px-6 py-5">
            <Field label="Продукт или услуга">
              <input
                required
                value={product}
                onChange={(e) => setProduct(e.target.value)}
                className={inputCls}
                placeholder="Доставка продуктов за 15 минут"
              />
            </Field>
            <Field label="Целевая аудитория">
              <input
                value={audience}
                onChange={(e) => setAudience(e.target.value)}
                className={inputCls}
                placeholder="Занятые жители крупных городов 25-45"
              />
            </Field>
            <Field label="Тональность">
              <select value={tone} onChange={(e) => setTone(e.target.value)} className={inputCls}>
                <option value="нейтральная">Нейтральная</option>
                <option value="дружелюбная">Дружелюбная</option>
                <option value="срочность и выгода">Срочность и выгода</option>
                <option value="премиальная">Премиальная</option>
              </select>
            </Field>
            {genError ? <p className="text-xs text-danger">{genError}</p> : null}
            <Button type="submit" disabled={genLoading} className="w-full">
              {genLoading ? "Генерация…" : "Сгенерировать"}
            </Button>
          </form>
          {variants ? (
            <div className="space-y-3 border-t border-border p-5">
              {variants.map((v, i) => (
                <div key={i} className="rounded-md border border-border bg-surface p-4">
                  <p className="text-sm font-semibold text-foreground">{v.title}</p>
                  <p className="mt-1 text-xs text-muted">{v.description}</p>
                  <span className="mt-2 inline-block rounded bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                    {v.cta}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
        </Card>

        <Card>
          <CardHeader title="AI-оптимизатор" subtitle="Анализ показателей за 7 дней и рекомендации" />
          <div className="px-6 py-5">
            <Button onClick={analyze} disabled={insLoading || !summary} className="w-full">
              {insLoading ? "Анализ…" : "Проанализировать аккаунт"}
            </Button>
            {insError ? <p className="mt-3 text-xs text-danger">{insError}</p> : null}
            {insLoading ? <Spinner /> : null}
            {insights ? (
              <div className="mt-4 space-y-3">
                {insights.map((ins, i) => (
                  <div key={i} className={`rounded-md border p-4 ${severityStyle[ins.severity]}`}>
                    <p className="text-xs font-medium uppercase tracking-wide text-muted">{severityLabel[ins.severity]}</p>
                    <p className="mt-1 text-sm font-semibold text-foreground">{ins.title}</p>
                    <p className="mt-1 text-xs leading-relaxed text-muted">{ins.recommendation}</p>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </Card>
      </div>
    </div>
  )
}
