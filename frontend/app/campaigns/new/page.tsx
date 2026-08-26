"use client"

import { useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import useSWR from "swr"
import { api, fetcher } from "@/lib/api"
import { Button, Card, CardHeader, Field, inputCls } from "@/components/ui"
import { CreativeBuilder, type CreativeDraft, FORMAT_LABELS } from "@/components/creative-builder"
import { TargetingEditor, targetingSummary } from "@/components/targeting-editor"
import type { Targeting } from "@/lib/types"

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

const STEPS = ["Формат", "Основное", "Бюджет и ставка", "Таргетинг", "Креативы"]


const KKTU_CATEGORIES: { code: string; label: string }[] = [
  { code: "1", label: "Продукты питания и напитки" },
  { code: "2", label: "Алкоголь (ограничено)" },
  { code: "3", label: "Одежда, обувь, аксессуары" },
  { code: "4", label: "Бытовая техника и электроника" },
  { code: "5", label: "Мебель и товары для дома" },
  { code: "6", label: "Строительство и ремонт" },
  { code: "7", label: "Автомобили и транспорт" },
  { code: "8", label: "Недвижимость" },
  { code: "9", label: "Финансовые услуги и банки" },
  { code: "10", label: "Страхование" },
  { code: "11", label: "Медицина и фармацевтика" },
  { code: "12", label: "Красота и уход" },
  { code: "13", label: "Спорт и фитнес" },
  { code: "14", label: "Туризм и путешествия" },
  { code: "15", label: "Образование" },
  { code: "16", label: "Развлечения и досуг" },
  { code: "17", label: "IT, софт и интернет-сервисы" },
  { code: "18", label: "Телеком и связь" },
  { code: "19", label: "Маркетплейсы и ритейл" },
  { code: "20", label: "Услуги для бизнеса (B2B)" },
  { code: "21", label: "Работа и HR" },
  { code: "22", label: "Детские товары" },
  { code: "23", label: "Зоотовары" },
  { code: "24", label: "Азартные игры, лотереи (ограничено)" },
  { code: "25", label: "Прочее" },
]

export default function NewCampaignPage() {
  const router = useRouter()
  const { data: advertisers } = useSWR<Advertiser[]>("/advertisers", fetcher)
  const { data: groups } = useSWR<Group[]>("/groups", fetcher)

  const [step, setStep] = useState(0)
  // Step 0: format
  const [format, setFormat] = useState("")
  // Step 1: basics
  const [name, setName] = useState("")
  const [advertiserId, setAdvertiserId] = useState("")
  const [objective, setObjective] = useState("awareness")
  const [kktu, setKktu] = useState("")
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
  // Step 4: creatives
  const [creatives, setCreatives] = useState<CreativeDraft[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")

  const canNext = useMemo(() => {
    if (step === 0) return !!format
    if (step === 1) return !!name.trim() && !!advertiserId
    if (step === 2) return Number(dailyBudget) > 0 && Number(bidPrice) > 0
    if (step === 3) return true // targeting is optional (empty = no restrictions)
    return creatives.length > 0
  }, [step, format, name, advertiserId, dailyBudget, bidPrice, creatives])

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
          kktu_code: kktu || null,
          kktu_label: kktu ? (KKTU_CATEGORIES.find((k) => k.code === kktu)?.label ?? null) : null,
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
        await api("/creatives", { method: "POST", body: { ...cr, campaign_id: camp.id, line_item_id: li.id } })
      }
      await api(`/campaigns/${camp.id}`, { method: "PATCH", body: { status: "active" } })
      router.push(`/campaigns/${camp.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка запуска")
      setSaving(false)
    }
  }

  return (
    <main className="p-4 md:p-6">
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

      {step === 1 ? (
        <Card>
          <CardHeader title="Основные настройки" subtitle={`Формат: ${FORMAT_LABELS[format] || format}`} />
          <div className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2">
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

            <Field label="Тематика товара/услуги (ККТУ для ОРД)" hint="Обязательно для РФ-кампаний — попадёт в маркировку">
              <select className={inputCls} value={kktu} onChange={(e) => setKktu(e.target.value)}>
                <option value="">Не выбрано</option>
                {KKTU_CATEGORIES.map((k) => (
                  <option key={k.code} value={k.code}>{k.label}</option>
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
            <Field label="Дата старта" hint="Пусто — сразу после запуска">
              <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className={inputCls} />
            </Field>
            <Field label="Дата окончания" hint="Пусто — без ограничения">
              <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className={inputCls} />
            </Field>
            <Field label="Темп расходования (pacing)">
              <select value={pacing} onChange={(e) => setPacing(e.target.value)} className={inputCls}>
                <option value="even">Равномерно в течение дня</option>
                <option value="asap">Как можно быстрее (ASAP)</option>
              </select>
            </Field>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-foreground">Частотные капы</span>
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
              {freqCaps.length === 0 ? (
                <p className="text-xs text-muted-foreground">{"Без лимита. Добавьте до 3 окон — например, 3 показа в день и 10 в неделю."}</p>
              ) : null}
              {freqCaps.map((w, i) => (
                <div key={i} className="flex items-end gap-2">
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
            </div>
            <label className="flex items-start gap-2 text-sm text-foreground">
              <input type="checkbox" checked={bidShading} onChange={(e) => setBidShading(e.target.checked)} className="mt-0.5 h-4 w-4 rounded border-border" />
              <span>
                Bid-шейдинг
                <span className="block text-xs text-muted-foreground">
                  {"Автоматически снижает ставку ближе к реальной цене клиринга (first-price). Экономия 15–30% без потери объёма — факторы пересчитываются ежечасно по каждой SSP."}
                </span>
              </span>
            </label>
          </div>
        </Card>
      ) : null}

      {step === 2 ? (
        <div className="space-y-4">
          <Card>
            <CardHeader title="Бюджет и ставка" subtitle="Ставка используется биддером в RTB-аукционе" />
            <div className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2">
              <Field label="Дневной бюджет, USD">
                <input type="number" min="1" step="1" value={dailyBudget} onChange={(e) => setDailyBudget(e.target.value)} className={inputCls} placeholder="100" />
              </Field>
              <Field label="Общий бюджет, USD" hint="Пусто = дневной × 30">
                <input type="number" min="0" step="1" value={totalBudget} onChange={(e) => setTotalBudget(e.target.value)} className={inputCls} placeholder="3000" />
              </Field>
              <Field label="Тип ставки">
                <select value={bidType} onChange={(e) => setBidType(e.target.value as "cpm" | "cpc")} className={inputCls}>
                  <option value="cpm">CPM — за 1000 показов</option>
                  <option value="cpc">CPC — за клик</option>
                </select>
              </Field>
              <Field label={bidType === "cpm" ? "Ставка CPM, USD" : "Ставка CPC, USD"}>
                <input type="number" min="0.01" step="0.01" value={bidPrice} onChange={(e) => setBidPrice(e.target.value)} className={inputCls} placeholder={bidType === "cpm" ? "2.50" : "0.15"} />
              </Field>
              <Field label="Макс. ставка, USD" hint="Потолок для аукциона; пусто — без потолка">
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
                <Field label="Целевой CPA, USD">
                  <input type="number" min="0" step="0.01" value={targetCpa} onChange={(e) => setTargetCpa(e.target.value)} className={inputCls} placeholder="—" />
                </Field>
                <Field label="Целевой CPC, USD">
                  <input type="number" min="0" step="0.001" value={targetCpc} onChange={(e) => setTargetCpc(e.target.value)} className={inputCls} placeholder="—" />
                </Field>
                <Field label="Целевой CPM, USD">
                  <input type="number" min="0" step="0.01" value={targetCpm} onChange={(e) => setTargetCpm(e.target.value)} className={inputCls} placeholder="—" />
                </Field>
                <Field label="Целевой CPV, USD">
                  <input type="number" min="0" step="0.001" value={targetCpv} onChange={(e) => setTargetCpv(e.target.value)} className={inputCls} placeholder="—" />
                </Field>
              </div>
            </div>
          </Card>
        </div>
      ) : null}

      {step === 3 ? (
        <Card>
          <CardHeader title="Таргетинг" subtitle="Пустые поля — без ограничений. Всё применяется биддером к каждому bid request" />
          <div className="p-4">
            <TargetingEditor value={targeting} onChange={setTargeting} />
          </div>
        </Card>
      ) : null}

      {step === 4 ? (
        <div className="space-y-4">
          <Card>
            <CardHeader title="Сводка" subtitle="Проверьте настройки перед запуском" />
            <dl className="grid grid-cols-1 gap-x-6 gap-y-2 p-4 text-sm sm:grid-cols-2">
              <div className="flex justify-between gap-3"><dt className="text-muted">Формат</dt><dd className="text-foreground">{FORMAT_LABELS[format] || format}</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-muted">Цель</dt><dd className="text-foreground">{OBJECTIVES.find((o) => o.key === objective)?.label}</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-muted">Бюджет/день</dt><dd className="text-foreground">${dailyBudget}</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-muted">Ставка</dt><dd className="text-foreground">{bidType.toUpperCase()} ${bidPrice}</dd></div>
              <div className="flex justify-between gap-3 sm:col-span-2"><dt className="text-muted">Таргетинг</dt><dd className="text-right text-foreground">{targetingSummary(targeting)}</dd></div>
            </dl>
          </Card>
          <CreativeBuilder format={format} creatives={creatives} onChange={setCreatives} />
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
              {step < 4 ? (
                <Button className="flex-1" onClick={() => setStep(step + 1)} disabled={!canNext}>
                  Далее
                </Button>
              ) : (
                <Button className="flex-1" onClick={launch} disabled={!canNext || saving}>
                  {saving ? "Запуск…" : `Запустить (креативов: ${creatives.length})`}
                </Button>
              )}
            </div>
          </div>
        </aside>
      </div>
    </main>
  )
}
