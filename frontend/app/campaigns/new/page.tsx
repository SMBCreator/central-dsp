"use client"

import { useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import useSWR from "swr"
import { api, fetcher } from "@/lib/api"
import { Button, Card, CardHeader, Field, HelpTip, inputCls } from "@/components/ui"
import { CreativeBuilder, type CreativeDraft, FORMAT_LABELS } from "@/components/creative-builder"
import { TargetingEditor, targetingSummary } from "@/components/targeting-editor"
import { KktuSelect } from "@/components/kktu-select"
import { EMPTY_CHAIN, OrdChainEditor, OrdChainSummaryView, type OrdChainSummary, type OrdChainValues } from "@/components/ord-chain-setup"
import { kktuShortLabel } from "@/lib/kktu"
import type { Counterparty, Targeting } from "@/lib/types"

type Advertiser = { id: string; name: string }
type Group = { id: string; name: string }

const FORMATS: { key: string; title: string; desc: string }[] = [
  { key: "banner", title: "Баннер", desc: "Статичные, GIF и HTML5 дисплей-баннеры стандартных размеров IAB" },
  { key: "native", title: "Нативная реклама", desc: "Заголовок, описание, изображение и иконка — встраивается в контент площадки" },
  { key: "video", title: "Видео", desc: "In-stream (внутри плеера) и out-stream (в контенте) ролики по VAST" },
  { key: "push", title: "Push-уведомления", desc: "Заголовок, текст, иконка и большая картинка — для push-трафика арбитражных сетей" },
]

const OBJECTIVES = [
  { key: "awareness", label: "Охват (CPM)" },
  { key: "traffic", label: "Трафик (CPC)" },
  { key: "conversions", label: "Конверсии (CPA)" },
]

const STEPS = ["Данные ОРД", "Формат", "Основное", "Бюджет и ставка", "Таргетинг", "Креативы"]


export default function NewCampaignPage() {
  const router = useRouter()
  const { data: advertisers } = useSWR<Advertiser[]>("/advertisers", fetcher)
  const { data: groups } = useSWR<Group[]>("/groups", fetcher)

  const [step, setStep] = useState(0)
  // Шаг 0 — данные ОРД (B13d): режим/рекламодатель/договоры → превью → «Всё верно».
  const [chain, setChain] = useState<OrdChainValues>(EMPTY_CHAIN)
  const [chainPreview, setChainPreview] = useState<OrdChainSummary | null>(null)
  const [chainReview, setChainReview] = useState(false)
  const [chainConfirmed, setChainConfirmed] = useState(false)
  const [chainBusy, setChainBusy] = useState(false)
  // Step 0: format
  const [format, setFormat] = useState("")
  // Step 1: basics
  const [name, setName] = useState("")
  const [advertiserId, setAdvertiserId] = useState("")
  const [objective, setObjective] = useState("awareness")
  const [groupId, setGroupId] = useState("")
  const [newGroupName, setNewGroupName] = useState("")
  const [startDate, setStartDate] = useState("")
  const [endDate, setEndDate] = useState("")
  const [pacing, setPacing] = useState("even")
  const [bidShading, setBidShading] = useState(false)
  const [freqCaps, setFreqCaps] = useState<{ imps: string; hours: string }[]>([])
  // Step 2: budget + bid + optimizer targets
  const [dailyBudget, setDailyBudget] = useState("")
  const [totalBudget, setTotalBudget] = useState("")
  const [bidType, setBidType] = useState<"cpm" | "cpc">("cpm")
  const [bidPrice, setBidPrice] = useState("")
  const [maxBid, setMaxBid] = useState("")
  const [targetCpa, setTargetCpa] = useState("")
  const [targetCpc, setTargetCpc] = useState("")
  const [targetCpm, setTargetCpm] = useState("")
  const [targetCpv, setTargetCpv] = useState("")
  const [autoOptimize, setAutoOptimize] = useState(true)
  // Step 3: full targeting
  const [targeting, setTargeting] = useState<Targeting>({})
  // Step 4: creatives + ОРД-маркировка (общая для всех креативов кампании;
  // без ККТУ и контрагента /creatives примет запись, но регистрация erid в ОРД не пройдёт —
  // поэтому мастер требует то же, что и модалка «Новый креатив»)
  const [creatives, setCreatives] = useState<CreativeDraft[]>([])
  const [kktuCode, setKktuCode] = useState("")
  const [kktuLabel, setKktuLabel] = useState("")
  const { data: counterparties } = useSWR<Counterparty[]>(step === 5 ? "/ord/counterparties" : null, fetcher)
  const selectedCp = useMemo(() => (counterparties || []).find((cp) => cp.id === chain.counterparty_id) || null, [counterparties, chain.counterparty_id])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")

  const canNext = useMemo(() => {
    if (step === 0) return chainConfirmed
    if (step === 1) return !!format
    if (step === 2) return !!name.trim() && !!advertiserId
    if (step === 3) return Number(dailyBudget) > 0 && Number(bidPrice) > 0
    if (step === 4) return true // targeting is optional (empty = no restrictions)
    return creatives.length > 0 && !!kktuCode
  }, [step, chainConfirmed, format, name, advertiserId, dailyBudget, bidPrice, creatives, kktuCode])

  // Превью цепочки считает бэкенд (тот же валидатор, что и подтверждение).
  // Рекламодатель (аккаунт) ещё не выбран на шаге 0 — берём первого доступного:
  // владелец всех справочников ОРД один и тот же для текущего пользователя.
  const previewChain = async () => {
    setChainBusy(true)
    setError("")
    try {
      const advId = advertiserId || advertisers?.[0]?.id
      if (!advId) throw new Error("Сначала создайте рекламодателя (аккаунт) в разделе «Рекламодатели»")
      const s = await api<OrdChainSummary>("/campaigns/ord-chain/preview", { method: "POST", body: { advertiser_id: advId, ...chain } })
      setChainPreview(s)
      setChainReview(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось проверить данные ОРД")
    } finally {
      setChainBusy(false)
    }
  }

  const launch = async () => {
    setSaving(true)
    setError("")
    try {
      let gid = groupId || null
      if (!gid && newGroupName.trim()) {
        const g = await api<Group>("/groups", { method: "POST", body: { name: newGroupName.trim() } })
        gid = g.id
      }
      const camp = await api<{ id: string }>("/campaigns", {
        method: "POST",
        body: {
          advertiser_id: advertiserId,
          ...chain,
          name: name.trim(),
          objective,
          daily_budget: Number(dailyBudget),
          total_budget: totalBudget ? Number(totalBudget) : Number(dailyBudget) * 30,
          group_id: gid,
          start_date: startDate || null,
          end_date: endDate || null,
          pacing,
          bid_shading: bidShading,
          frequency_caps: freqCaps.filter((w) => Number(w.imps) > 0).length
            ? freqCaps
                .filter((w) => Number(w.imps) > 0)
                .slice(0, 3)
                .map((w) => ({ impressions: Number(w.imps), period_hours: Number(w.hours) || 24 }))
            : null,
        },
      })
      // Цепочка ОРД подтверждена на шаге 0 — фиксируем на созданной кампании.
      await api(`/campaigns/${camp.id}/ord-chain/confirm`, { method: "POST" })
      // Optimizer targets + auto-optimize
      if (targetCpa || targetCpc || targetCpm || targetCpv || autoOptimize) {
        await api(`/campaigns/${camp.id}`, {
          method: "PATCH",
          body: {
            target_cpa: targetCpa ? Number(targetCpa) : null,
            target_cpc: targetCpc ? Number(targetCpc) : null,
            target_cpm: targetCpm ? Number(targetCpm) : null,
            target_cpv: targetCpv ? Number(targetCpv) : null,
            auto_optimize: autoOptimize,
          },
        })
      }
      // Line item: bid + full targeting (this is what the bidder matches on)
      const li = await api<{ id: string }>("/line-items", {
        method: "POST",
        body: {
          campaign_id: camp.id,
          name: `${name.trim()} — закупка`,
          bid_type: bidType,
          bid_price: Number(bidPrice),
          max_bid: maxBid ? Number(maxBid) : null,
          daily_budget: Number(dailyBudget),
          targeting,
        },
      })
      await api(`/line-items/${li.id}`, { method: "PATCH", body: { status: "active" } })
      for (const cr of creatives) {
        await api("/creatives", {
          method: "POST",
          body: {
            ...cr,
            campaign_id: camp.id,
            line_item_id: li.id,
            counterparty_id: chain.counterparty_id,
            kktu_code: kktuCode || null,
            kktu_label: kktuLabel || null,
          },
        })
      }
      await api(`/campaigns/${camp.id}`, { method: "PATCH", body: { status: "active" } })
      router.push(`/campaigns/${camp.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка запуска")
      setSaving(false)
    }
  }

  return (
    <div className="p-4 md:p-6">
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-foreground">Новая кампания</h1>
        <p className="mt-0.5 text-sm text-muted">
          Шаг {step + 1} из {STEPS.length}: {STEPS[step]}
        </p>
      </header>
      <div className="flex flex-col gap-6 lg:flex-row">
        <div className="min-w-0 flex-1 space-y-6">

      {step === 0 ? (
        <Card>
          <CardHeader
            title={chainReview ? "Проверьте данные для ОРД" : "Данные для ОРД"}
            subtitle={chainReview
              ? "Так цепочка уйдёт в ОРД при регистрации креативов. После подтверждения изменить нельзя — для другого рекламодателя создаётся новая кампания."
              : "По 38-ФЗ каждое объявление маркируется: рекламодатель и договоры регистрируются в ОРД. Заполните один раз для кампании."}
          />
          {chainReview && chainPreview ? (
            <>
              <OrdChainSummaryView summary={chainPreview} />
              <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
                <Button type="button" variant="secondary" onClick={() => { setChainReview(false); setChainConfirmed(false) }}>Изменить</Button>
                <Button type="button" disabled={!chainPreview.ready} onClick={() => { setChainConfirmed(true); setStep(1) }}>
                  Всё верно
                </Button>
              </div>
            </>
          ) : (
            <>
              <OrdChainEditor value={chain} onChange={(v) => { setChain(v); setChainConfirmed(false) }} />
              <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
                <Button type="button" disabled={!chain.ord_mode || !chain.counterparty_id || chainBusy} onClick={previewChain}>
                  {chainBusy ? "Проверка…" : "Проверить данные →"}
                </Button>
              </div>
            </>
          )}
        </Card>
      ) : null}

      {step === 1 ? (
        <Card>
          <CardHeader title="Выберите формат" subtitle="Формат определяет, какие креативы вы загрузите на последнем шаге" />
          <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2">
            {FORMATS.map((f) => (
              <button
                key={f.key}
                type="button"
                onClick={() => setFormat(f.key)}
                className={`rounded-2xl border p-4 text-left transition-colors ${
                  format === f.key ? "border-foreground bg-panel" : "border-border hover:bg-panel"
                }`}
                aria-pressed={format === f.key}
              >
                <p className="text-sm font-semibold text-foreground">{f.title}</p>
                <p className="mt-1 text-xs leading-relaxed text-muted">{f.desc}</p>
              </button>
            ))}
          </div>
        </Card>
      ) : null}

      {step === 2 ? (
        <Card>
          <CardHeader title="Основные настройки" subtitle={`Формат: ${FORMAT_LABELS[format] || format}`} />
          <div className="divide-y divide-border">
            {/* Секция 1: о кампании */}
            <section className="space-y-4 p-4">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">О кампании</h3>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field label="Название кампании">
                  <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} placeholder="Осенняя распродажа" />
                </Field>
                <Field label="Рекламодатель">
                  <select value={advertiserId} onChange={(e) => setAdvertiserId(e.target.value)} className={inputCls}>
                    <option value="">Выберите…</option>
                    {(advertisers || []).map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Цель">
                  <select value={objective} onChange={(e) => setObjective(e.target.value)} className={inputCls}>
                    {OBJECTIVES.map((o) => (
                      <option key={o.key} value={o.key}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Группа объявлений" hint="Объединяет несколько кампаний для сводной статистики">
                  <select value={groupId} onChange={(e) => setGroupId(e.target.value)} className={inputCls}>
                    <option value="">Без группы</option>
                    {(groups || []).map((g) => (
                      <option key={g.id} value={g.id}>
                        {g.name}
                      </option>
                    ))}
                  </select>
                </Field>
                {!groupId ? (
                  <Field label="…или создать новую группу">
                    <input value={newGroupName} onChange={(e) => setNewGroupName(e.target.value)} className={inputCls} placeholder="Название группы" />
                  </Field>
                ) : null}
              </div>
            </section>

            {/* Секция 2: период и темп — даты в одной строке */}
            <section className="space-y-4 p-4">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Период и темп открутки</h3>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <div className="grid grid-cols-2 gap-3 sm:col-span-2">
                  <Field label="Дата старта" hint="Пусто — сразу после запуска">
                    <input type="date" value={startDate} max={endDate || undefined} onChange={(e) => setStartDate(e.target.value)} className={inputCls} />
                  </Field>
                  <Field label="Дата окончания" hint="Пусто — без ограничения">
                    <input type="date" value={endDate} min={startDate || undefined} onChange={(e) => setEndDate(e.target.value)} className={inputCls} />
                  </Field>
                </div>
                <Field label="Темп расходования (pacing)">
                  <select value={pacing} onChange={(e) => setPacing(e.target.value)} className={inputCls}>
                    <option value="even">Равномерно в течение дня</option>
                    <option value="asap">Как можно быстрее (ASAP)</option>
                  </select>
                </Field>
              </div>
            </section>

            {/* Секция 3: ограничения показов */}
            <section className="space-y-4 p-4">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Ограничения показов</h3>
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                    Частотные капы
                    <HelpTip text="Ограничение показов одному пользователю. До 3 окон — например, 3 показа в день и 10 в неделю. Пусто — без лимита." />
                  </span>
                  {freqCaps.length < 3 ? (
                    <button
                      type="button"
                      onClick={() => setFreqCaps([...freqCaps, { imps: "", hours: freqCaps.length === 0 ? "24" : "168" }])}
                      className="text-xs font-medium text-primary hover:underline"
                    >
                      + Добавить окно
                    </button>
                  ) : null}
                </div>
                {freqCaps.length === 0 ? <p className="text-xs text-muted-foreground">Без лимита</p> : null}
                {freqCaps.map((w, i) => (
                  <div key={i} className="flex items-end gap-2 sm:max-w-md">
                    <div className="flex-1">
                      <label className="mb-1 block text-xs text-muted-foreground">Показов</label>
                      <input
                        type="number"
                        min="1"
                        max="1000"
                        value={w.imps}
                        onChange={(e) => setFreqCaps(freqCaps.map((x, j) => (j === i ? { ...x, imps: e.target.value } : x)))}
                        className={inputCls}
                        placeholder="3"
                      />
                    </div>
                    <div className="flex-1">
                      <label className="mb-1 block text-xs text-muted-foreground">Период</label>
                      <select
                        value={w.hours}
                        onChange={(e) => setFreqCaps(freqCaps.map((x, j) => (j === i ? { ...x, hours: e.target.value } : x)))}
                        className={inputCls}
                      >
                        <option value="1">Час</option>
                        <option value="24">Сутки</option>
                        <option value="168">Неделя</option>
                        <option value="720">Месяц</option>
                      </select>
                    </div>
                    <button
                      type="button"
                      onClick={() => setFreqCaps(freqCaps.filter((_, j) => j !== i))}
                      className="h-8 rounded-lg border border-border px-2 text-xs text-muted-foreground hover:bg-accent"
                      aria-label="Удалить окно"
                    >
                      Удалить
                    </button>
                  </div>
                ))}
                <label className="flex items-start gap-2 pt-2 text-sm text-foreground">
                  <input type="checkbox" checked={bidShading} onChange={(e) => setBidShading(e.target.checked)} className="mt-0.5 h-4 w-4 rounded border-border" />
                  <span className="flex items-center gap-1.5">
                    Bid-шейдинг
                    <HelpTip text="Автоматически снижает ставку ближе к реальной цене клиринга (first-price). Экономия 15–30% без потери объёма — факторы пересчитываются ежечасно по каждой SSP." />
                  </span>
                </label>
              </div>
            </section>
          </div>
        </Card>
      ) : null}

      {step === 3 ? (
        <div className="space-y-4">
          <Card>
            <CardHeader title="Бюджет и ставка" subtitle="Ставка используется биддером в RTB-аукционе" />
            <div className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">
              <Field label="Дневной бюджет, ₽">
                <input type="number" min="1" step="1" value={dailyBudget} onChange={(e) => setDailyBudget(e.target.value)} className={inputCls} placeholder="100" />
              </Field>
              <Field label="Общий бюджет, ₽" hint="Пусто = дневной × 30">
                <input type="number" min="0" step="1" value={totalBudget} onChange={(e) => setTotalBudget(e.target.value)} className={inputCls} placeholder="3000" />
              </Field>
              <Field label="Тип ставки">
                <select value={bidType} onChange={(e) => setBidType(e.target.value as "cpm" | "cpc")} className={inputCls}>
                  <option value="cpm">CPM — за 1000 показов</option>
                  <option value="cpc">CPC — за клик</option>
                </select>
              </Field>
              <Field label={bidType === "cpm" ? "Ставка CPM, ₽" : "Ставка CPC, ₽"}>
                <input type="number" min="0.01" step="0.01" value={bidPrice} onChange={(e) => setBidPrice(e.target.value)} className={inputCls} placeholder={bidType === "cpm" ? "2.50" : "0.15"} />
              </Field>
              <Field label="Макс. ставка, ₽" hint="Потолок для аукциона; пусто — без потолка">
                <input type="number" min="0.01" step="0.01" value={maxBid} onChange={(e) => setMaxBid(e.target.value)} className={inputCls} placeholder="Не задан" />
              </Field>
            </div>
          </Card>
          <Card>
            <CardHeader title="Автооптимизация" subtitle="Оптимизатор блокирует источники, не укладывающиеся в целевые метрики" />
            <div className="space-y-4 p-4">
              <label className="flex items-center gap-2 text-sm text-foreground">
                <input type="checkbox" checked={autoOptimize} onChange={(e) => setAutoOptimize(e.target.checked)} className="h-4 w-4 rounded border-border" />
                Включить автооптимизацию закупки
              </label>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Field label="Целевой CPA, ₽">
                  <input type="number" min="0" step="0.01" value={targetCpa} onChange={(e) => setTargetCpa(e.target.value)} className={inputCls} placeholder="—" />
                </Field>
                <Field label="Целевой CPC, ₽">
                  <input type="number" min="0" step="0.001" value={targetCpc} onChange={(e) => setTargetCpc(e.target.value)} className={inputCls} placeholder="—" />
                </Field>
                <Field label="Целевой CPM, ₽">
                  <input type="number" min="0" step="0.01" value={targetCpm} onChange={(e) => setTargetCpm(e.target.value)} className={inputCls} placeholder="—" />
                </Field>
                <Field label="Целевой CPV, ₽">
                  <input type="number" min="0" step="0.001" value={targetCpv} onChange={(e) => setTargetCpv(e.target.value)} className={inputCls} placeholder="—" />
                </Field>
              </div>
            </div>
          </Card>
        </div>
      ) : null}

      {step === 4 ? (
        <Card>
          <CardHeader title="Таргетинг" subtitle="Пустые поля — без ограничений. Всё применяется биддером к каждому bid request" />
          <div className="p-4">
            <TargetingEditor value={targeting} onChange={setTargeting} />
          </div>
        </Card>
      ) : null}

      {step === 5 ? (
        <div className="space-y-4">
          <Card>
            <CardHeader title="Сводка" subtitle="Проверьте настройки перед запуском" />
            <dl className="grid grid-cols-1 gap-x-6 gap-y-2 p-4 text-sm sm:grid-cols-2">
              <div className="flex justify-between gap-3"><dt className="text-muted">Формат</dt><dd className="text-foreground">{FORMAT_LABELS[format] || format}</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-muted">Цель</dt><dd className="text-foreground">{OBJECTIVES.find((o) => o.key === objective)?.label}</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-muted">Бюджет/день</dt><dd className="text-foreground">{dailyBudget} ₽</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-muted">Ставка</dt><dd className="text-foreground">{bidType.toUpperCase()} {bidPrice} ₽</dd></div>
              <div className="flex justify-between gap-3 sm:col-span-2"><dt className="text-muted">Таргетинг</dt><dd className="text-right text-foreground">{targetingSummary(targeting)}</dd></div>
            </dl>
          </Card>
          <CreativeBuilder format={format} creatives={creatives} onChange={setCreatives} />

          <Card>
            <CardHeader
              title="Маркировка (ОРД)"
              subtitle="Применяется ко всем креативам кампании. По 38-ФЗ данные рекламодателя показываются в инфо-значке на креативе и уходят в ОРД при регистрации erid."
            />
            <div className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2">
              <Field label="Рекламодатель (из данных ОРД)" hint="Задан на шаге «Данные ОРД» и наследуется всеми креативами кампании">
                <input className={inputCls} disabled value={selectedCp ? `${selectedCp.name}${selectedCp.inn ? ` · ИНН ${selectedCp.inn}` : ""}` : "—"} />
              </Field>
              <Field label="Классификатор ККТУ *">
                <KktuSelect
                  value={kktuCode}
                  onChange={(code, label) => {
                    setKktuCode(code)
                    setKktuLabel(label)
                  }}
                />
              </Field>
              <div className="sm:col-span-2">
                {selectedCp ? (
                  <dl className="grid grid-cols-1 gap-x-6 gap-y-1 rounded-lg bg-panel px-3 py-2 text-xs sm:grid-cols-2">
                    <div className="flex gap-1.5">
                      <dt className="text-muted">ИНН:</dt>
                      <dd className="text-foreground">{selectedCp.inn || "—"}</dd>
                    </div>
                    <div className="flex gap-1.5">
                      <dt className="text-muted">ОГРН:</dt>
                      <dd className="text-foreground">{selectedCp.ogrn || "—"}</dd>
                    </div>
                    <div className="flex gap-1.5 sm:col-span-2">
                      <dt className="text-muted">Адрес:</dt>
                      <dd className="text-foreground">{selectedCp.address || "—"}</dd>
                    </div>
                  </dl>
                ) : (
                  <p className="text-xs text-warning">Рекламодатель не задан — вернитесь на шаг «Данные ОРД».</p>
                )}
                <p className="mt-2 text-xs text-muted">
                  {kktuCode ? (
                    <>
                      Категория: <span className="text-foreground">{kktuShortLabel(kktuCode)}</span>
                    </>
                  ) : (
                    "Обязательное поле: категория товара/услуги уходит в ОРД вместе с erid."
                  )}
                </p>
              </div>
            </div>
          </Card>
        </div>
      ) : null}

          {error ? (
            <p role="alert" className="rounded-2xl bg-panel px-4 py-3 text-sm text-danger">
              {error}
            </p>
          ) : null}
        </div>

        <aside className="w-full shrink-0 lg:w-72">
          <div className="sticky top-6 space-y-4">
            <Card className="p-4">
              <p className="mb-2 px-2.5 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
                Шаги настройки
              </p>
              <ol className="space-y-1" aria-label="Шаги мастера">
                {STEPS.map((s, i) => {
                  const done = i < step
                  const current = i === step
                  return (
                    <li key={s}>
                      <button
                        type="button"
                        onClick={() => (done ? setStep(i) : undefined)}
                        disabled={!done && !current}
                        aria-current={current ? "step" : undefined}
                        className={`flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors ${
                          current
                            ? "bg-panel font-medium text-foreground"
                            : done
                              ? "text-foreground hover:bg-panel"
                              : "text-muted"
                        }`}
                      >
                        <span
                          className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full font-mono text-[10px] ${
                            current
                              ? "bg-foreground text-background"
                              : done
                                ? "bg-success/15 text-success"
                                : "bg-panel text-muted"
                          }`}
                        >
                          {done ? "✓" : i + 1}
                        </span>
                        {s}
                      </button>
                    </li>
                  )
                })}
              </ol>
            </Card>
            <div className="flex items-center gap-2">
              <Button variant="ghost" onClick={() => (step === 0 ? router.push("/campaigns") : setStep(step - 1))}>
                {step === 0 ? "Отмена" : "Назад"}
              </Button>
              {step < STEPS.length - 1 ? (
                <Button className="flex-1" onClick={() => setStep(step + 1)} disabled={!canNext}>
                  Далее
                </Button>
              ) : (
                <Button className="flex-1" onClick={launch} disabled={!canNext || saving}>
                  {saving ? "Запуск…" : `Запустить (креативов: ${creatives.length})`}
                </Button>
              )}
            </div>
            {step === 5 && !canNext ? (
              <p className="text-[11px] leading-relaxed text-muted">
                {creatives.length === 0 ? "Добавьте хотя бы один креатив." : "Выберите классификатор ККТУ в блоке «Маркировка (ОРД)»."}
              </p>
            ) : null}
          </div>
        </aside>
      </div>
    </div>
  )
}
