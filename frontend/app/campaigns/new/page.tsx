"use client"

import { useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import useSWR from "swr"
import { api, fetcher } from "@/lib/api"
import { Button, Card, CardHeader, Field, inputCls } from "@/components/ui"
import { CreativeBuilder, type CreativeDraft, FORMAT_LABELS } from "@/components/creative-builder"

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

const STEPS = ["Формат", "Основное", "Бюджет", "Креативы"]

export default function NewCampaignPage() {
  const router = useRouter()
  const { data: advertisers } = useSWR<Advertiser[]>("/advertisers", fetcher)
  const { data: groups } = useSWR<Group[]>("/groups", fetcher)

  const [step, setStep] = useState(0)
  const [format, setFormat] = useState("")
  const [name, setName] = useState("")
  const [advertiserId, setAdvertiserId] = useState("")
  const [objective, setObjective] = useState("awareness")
  const [groupId, setGroupId] = useState("")
  const [newGroupName, setNewGroupName] = useState("")
  const [dailyBudget, setDailyBudget] = useState("")
  const [totalBudget, setTotalBudget] = useState("")
  const [creatives, setCreatives] = useState<CreativeDraft[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")

  const canNext = useMemo(() => {
    if (step === 0) return !!format
    if (step === 1) return !!name.trim() && !!advertiserId
    if (step === 2) return Number(dailyBudget) > 0
    return creatives.length > 0
  }, [step, format, name, advertiserId, dailyBudget, creatives])

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
          name: name.trim(),
          objective,
          daily_budget: Number(dailyBudget),
          total_budget: totalBudget ? Number(totalBudget) : Number(dailyBudget) * 30,
          group_id: gid,
        },
      })
      for (const cr of creatives) {
        await api("/creatives", { method: "POST", body: { ...cr, campaign_id: camp.id } })
      }
      router.push(`/campaigns/${camp.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка запуска")
      setSaving(false)
    }
  }

  return (
    <main className="mx-auto max-w-3xl space-y-6 p-4 md:p-8">
      <header>
        <h1 className="text-xl font-semibold text-foreground">Новая кампания</h1>
        <nav aria-label="Шаги мастера" className="mt-4 flex items-center gap-2">
          {STEPS.map((s, i) => (
            <div key={s} className="flex items-center gap-2">
              <span
                className={`flex h-6 w-6 items-center justify-center rounded-full font-mono text-[11px] ${
                  i === step
                    ? "bg-foreground text-background"
                    : i < step
                      ? "bg-panel text-foreground"
                      : "bg-panel text-muted"
                }`}
              >
                {i + 1}
              </span>
              <span className={`text-xs ${i === step ? "text-foreground" : "text-muted"}`}>{s}</span>
              {i < STEPS.length - 1 ? <span className="mx-1 h-px w-6 bg-border" aria-hidden /> : null}
            </div>
          ))}
        </nav>
      </header>

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
        </Card>
      ) : null}

      {step === 2 ? (
        <Card>
          <CardHeader title="Бюджет" subtitle="Дневной лимит обязателен, общий — опционален" />
          <div className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2">
            <Field label="Дневной бюджет, USD">
              <input type="number" min="1" step="1" value={dailyBudget} onChange={(e) => setDailyBudget(e.target.value)} className={inputCls} placeholder="100" />
            </Field>
            <Field label="Общий бюджет, USD" hint="Пусто = дневной × 30">
              <input type="number" min="0" step="1" value={totalBudget} onChange={(e) => setTotalBudget(e.target.value)} className={inputCls} placeholder="3000" />
            </Field>
          </div>
        </Card>
      ) : null}

      {step === 3 ? <CreativeBuilder format={format} creatives={creatives} onChange={setCreatives} /> : null}

      {error ? (
        <p role="alert" className="rounded-2xl bg-panel px-4 py-3 text-sm text-danger">
          {error}
        </p>
      ) : null}

      <div className="flex items-center justify-between">
        <Button variant="ghost" onClick={() => (step === 0 ? router.push("/campaigns") : setStep(step - 1))}>
          {step === 0 ? "Отмена" : "Назад"}
        </Button>
        {step < 3 ? (
          <Button onClick={() => setStep(step + 1)} disabled={!canNext}>
            Далее
          </Button>
        ) : (
          <Button onClick={launch} disabled={!canNext || saving}>
            {saving ? "Запуск…" : `Запустить кампанию (${creatives.length} креативов)`}
          </Button>
        )}
      </div>
    </main>
  )
}
