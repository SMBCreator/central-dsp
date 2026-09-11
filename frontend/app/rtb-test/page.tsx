"use client"

import { useState } from "react"
import Link from "next/link"
import useSWR, { mutate } from "swr"
import { api, ApiError, fetcher } from "@/lib/api"
import { AD_TYPE_LABELS, fmtNum, type SupplyPartner } from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Field, inputCls, Spinner } from "@/components/ui"

interface SimResult {
  results: {
    requests: number
    bids: number
    no_bids: number
    wins: number
    impressions: number
    clicks: number
    errors: number
  }
  sample: { request: unknown; response: unknown } | null
  /** Какой движок отвечал: боевой Go-биддер или Python reference (только dev, по явному запросу). */
  engine?: "go" | "python_reference"
  requested_engine?: "go" | "python_reference"
  /** Всегда false: автоматического переключения движка нет (C1d). */
  fallback_used?: boolean
  /** Все запросы шли с OpenRTB test=1 — деньги и статистика клиента не затронуты. */
  test?: boolean
}

export default function RtbTestPage() {
  const { data: partners } = useSWR<SupplyPartner[]>("/supply-partners", fetcher)
  const [endpointKey, setEndpointKey] = useState("")
  const [requests, setRequests] = useState("50")
  const [adType, setAdType] = useState("banner")
  const [geo, setGeo] = useState("RU")
  const [device, setDevice] = useState("mobile")
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<SimResult | null>(null)
  const [showSample, setShowSample] = useState(false)

  const run = async (e: React.FormEvent) => {
    e.preventDefault()
    setRunning(true)
    setError(null)
    setResult(null)
    try {
      const res = await api<SimResult>("/simulator/run", {
        method: "POST",
        body: {
          endpoint_key: endpointKey,
          requests: Number(requests),
          ad_type: adType,
          geo_country: geo,
          device_type: device,
        },
      })
      setResult(res)
      await mutate((key) => typeof key === "string" && key.startsWith("/stats"), undefined, { revalidate: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        setError(
          "Go-биддер недоступен — симуляция не выполнена. Автоматического переключения на Python reference нет; повторите после восстановления биддера.",
        )
      } else {
        setError(err instanceof Error ? err.message : "Ошибка симуляции")
      }
    } finally {
      setRunning(false)
    }
  }

  const activePartners = (partners || []).filter((p) => p.status === "active")

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">RTB-тестер</h1>
        <p className="mt-0.5 text-sm text-muted">
          Симулятор SSP: отправляет OpenRTB 2.5 BidRequest на ваш endpoint и показывает результат аукциона
        </p>
      </div>

      {partners && activePartners.length === 0 ? (
        <Card>
          <EmptyState
            title="Нет активных SSP-партнёров"
            description="Сначала подключите SSP — симулятор отправляет запросы на его endpoint."
            action={
              <Link href="/supply">
                <Button>Подключить SSP</Button>
              </Link>
            }
          />
        </Card>
      ) : null}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Параметры трафика" />
          <form onSubmit={run} className="space-y-4 p-5">
            <Field label="SSP-партнёр">
              <select
                required
                value={endpointKey}
                onChange={(e) => setEndpointKey(e.target.value)}
                className={inputCls}
              >
                <option value="" disabled>
                  Выберите…
                </option>
                {activePartners.map((p) => (
                  <option key={p.id} value={p.endpoint_key}>
                    {p.name}
                  </option>
                ))}
              </select>
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Количество запросов">
                <input
                  type="number"
                  min="1"
                  max="500"
                  value={requests}
                  onChange={(e) => setRequests(e.target.value)}
                  className={inputCls}
                />
              </Field>
              <Field label="Формат">
                <select value={adType} onChange={(e) => setAdType(e.target.value)} className={inputCls}>
                  {Object.entries(AD_TYPE_LABELS).map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Гео">
                <select value={geo} onChange={(e) => setGeo(e.target.value)} className={inputCls}>
                  {["RU", "KZ", "BY", "UZ", "KG", "AM", "AZ", "GE"].map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Устройство">
                <select value={device} onChange={(e) => setDevice(e.target.value)} className={inputCls}>
                  <option value="mobile">Мобильное</option>
                  <option value="desktop">Десктоп</option>
                  <option value="tablet">Планшет</option>
                </select>
              </Field>
            </div>
            {error ? <p className="text-xs text-danger">{error}</p> : null}
            <Button type="submit" disabled={running || !endpointKey} className="w-full">
              {running ? "Отправка запросов…" : "Запустить симуляцию"}
            </Button>
          </form>
        </Card>

        <Card>
          <CardHeader title="Результаты" subtitle="Воронка аукциона" />
          {running ? (
            <Spinner />
          ) : result ? (
            <div className="p-5">
              {result.test ? (
                <p className="mb-3 rounded-md border border-border bg-surface px-3 py-2 text-xs text-muted">
                  Тестовый режим (OpenRTB test=1): ставки, выигрыши и клики записаны в журнал с пометкой
                  «тест», но не списывают бюджет и не попадают в статистику кампаний.
                  {result.engine ? ` Движок: ${result.engine === "go" ? "боевой Go-биддер" : "Python reference (dev)"}.` : ""}
                </p>
              ) : null}
              <div className="space-y-2">
                {(
                  [
                    ["Запросов отправлено", result.results.requests],
                    ["Ставок сделано", result.results.bids],
                    ["Без ставки (no-bid)", result.results.no_bids],
                    ["Выигрышей", result.results.wins],
                    ["Показов", result.results.impressions],
                    ["Кликов", result.results.clicks],
                    ["Ошибок", result.results.errors],
                  ] as [string, number][]
                ).map(([label, value]) => (
                  <div key={label} className="flex items-center justify-between rounded-md bg-surface px-3 py-2">
                    <span className="text-xs text-muted">{label}</span>
                    <span className="font-mono text-sm font-semibold text-foreground">{fmtNum(value)}</span>
                  </div>
                ))}
              </div>
              {result.sample ? (
                <div className="mt-4">
                  <Button variant="secondary" onClick={() => setShowSample(!showSample)} className="w-full">
                    {showSample ? "Скрыть пример BidResponse" : "Показать пример BidRequest / BidResponse"}
                  </Button>
                  {showSample ? (
                    <pre className="mt-3 max-h-80 overflow-auto rounded-md bg-surface p-3 font-mono text-[11px] leading-relaxed text-muted">
                      {JSON.stringify(result.sample, null, 2)}
                    </pre>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : (
            <EmptyState
              title="Симуляция ещё не запускалась"
              description="Результаты появятся здесь. Если ставок нет — проверьте, что кампания активна, есть строка закупки и одобренный креатив."
            />
          )}
        </Card>
      </div>
    </div>
  )
}
