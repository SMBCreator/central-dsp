"use client"

import { use, useEffect, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import useSWR, { mutate } from "swr"
import { api, ApiError, fetcher } from "@/lib/api"
import { NotifyEmailsField } from "@/components/notify-emails-field"
import { emailsError, formatEmails, parseEmails } from "@/lib/notify-emails"
import { CopyId } from "@/components/copy-id"
import { toastSuccess } from "@/components/toaster"
import {
  fmtMoney,
  fmtNum,
  fmtPct,
  type Campaign,
  type Creative,
  type LineItem,
  type StatsSummary,
  type Targeting,
} from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Field, HelpTip, inputCls, KpiCard, Modal, Spinner, StatusBadge, Table } from "@/components/ui"
import { DEVICES, TargetingEditor } from "@/components/targeting-editor"
import { OrdChainCampaignCard, type OrdChainValues } from "@/components/ord-chain-setup"
import { useAdvertisers } from "@/lib/advertisers"
import { isForeignAdvertiser } from "@/lib/ad-market"

type PixelRow = { id: string; name: string; domain: string }
type GoalRow = { id: string; name: string; goal_type: string; match_value: string }
type Group = { id: string; name: string }
type BlockRow = {
  id: string
  block_type: string
  block_value: string
  reason: string
  partner_name?: string | null
  created_at: string
}
type CampaignExt = Campaign & {
  group_id?: string | null
  pacing?: string
  bid_shading?: boolean
  clean_traffic_only?: boolean
  scoring_profile_id?: string | null
  frequency_caps?: { impressions: number; period_hours: number }[] | null
  start_date?: string | null
  end_date?: string | null
  objective: string
}

const OBJECTIVES = [
  { value: "awareness", label: "Охват" },
  { value: "traffic", label: "Трафик" },
  { value: "conversions", label: "Конверсии" },
]

/* ---------- Основные настройки кампании ---------- */

function SettingsCard({ campaign }: { campaign: CampaignExt }) {
  const { data: groups } = useSWR<Group[]>("/groups", fetcher)
  const { data: scoringProfiles } = useSWR<{ id: string; name: string; enforce: boolean }[]>("/scoring/profiles", fetcher)
  const [scoringProfileId, setScoringProfileId] = useState(campaign.scoring_profile_id || "")
  const [name, setName] = useState(campaign.name)
  const [objective, setObjective] = useState(campaign.objective)
  const [groupId, setGroupId] = useState(campaign.group_id || "")
  const [dailyBudget, setDailyBudget] = useState(String(campaign.daily_budget ?? ""))
  const [totalBudget, setTotalBudget] = useState(campaign.total_budget ? String(campaign.total_budget) : "")
  const [startDate, setStartDate] = useState(campaign.start_date || "")
  const [endDate, setEndDate] = useState(campaign.end_date || "")
  const [pacing, setPacing] = useState(campaign.pacing || "even")
  const [bidShading, setBidShading] = useState(campaign.bid_shading !== false)
  const [cleanTraffic, setCleanTraffic] = useState(!!campaign.clean_traffic_only)
  const [notifyEmails, setNotifyEmails] = useState(formatEmails(campaign.notify_emails))
  const [notifyInherit, setNotifyInherit] = useState(campaign.notify_inherit !== false)
  const [freqCaps, setFreqCaps] = useState<{ imps: string; hours: string }[]>(
    (campaign.frequency_caps || []).map((w) => ({ imps: String(w.impressions), hours: String(w.period_hours) })),
  )
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Синхронизация при обновлении кампании после сохранения
  useEffect(() => {
    setName(campaign.name)
    setObjective(campaign.objective)
    setGroupId(campaign.group_id || "")
  }, [campaign.name, campaign.objective, campaign.group_id])

  const save = async () => {
    const parsedEmails = parseEmails(notifyEmails)
    const emailsProblem = emailsError(parsedEmails)
    if (emailsProblem) {
      setError(emailsProblem)
      return
    }
    setSaving(true)
    setSaved(false)
    setError(null)
    try {
      const caps = freqCaps
        .filter((w) => w.imps && Number(w.imps) > 0)
        .map((w) => ({ impressions: Number(w.imps), period_hours: Number(w.hours) }))
      await api(`/campaigns/${campaign.id}`, {
        method: "PATCH",
        body: {
          name: name.trim(),
          objective,
          group_id: groupId || null,
          daily_budget: dailyBudget ? Number(dailyBudget) : undefined,
          total_budget: totalBudget ? Number(totalBudget) : undefined,
          start_date: startDate || null,
          end_date: endDate || null,
          pacing,
          bid_shading: bidShading,
          clean_traffic_only: cleanTraffic,
          scoring_profile_id: scoringProfileId || null,
          frequency_caps: caps,
          notify_emails: parsedEmails.emails,
          notify_inherit: notifyInherit,
        },
      })
      await mutate(`/campaigns/${campaign.id}`)
      await mutate("/campaigns")
      setSaved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка сохранения")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader title="Основные настройки" subtitle="Название, бюджеты, сроки, частотные капы" />
      <div className="space-y-4 px-6 py-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Название кампании">
            <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} />
          </Field>
          <Field label="Цель">
            <select value={objective} onChange={(e) => setObjective(e.target.value)} className={inputCls}>
              {OBJECTIVES.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Группа кампаний">
            <select value={groupId} onChange={(e) => setGroupId(e.target.value)} className={inputCls}>
              <option value="">Без группы</option>
              {(groups || []).map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Дневной бюджет, ₽">
            <input type="number" min="1" step="1" value={dailyBudget} onChange={(e) => setDailyBudget(e.target.value)} className={inputCls} />
          </Field>
          <Field label="Общий бюджет, ₽">
            <input type="number" min="0" step="1" value={totalBudget} onChange={(e) => setTotalBudget(e.target.value)} className={inputCls} placeholder="Без ограничения" />
          </Field>
          <Field label="Темп расходования">
            <select value={pacing} onChange={(e) => setPacing(e.target.value)} className={inputCls}>
              <option value="even">Равномерно в течение дня</option>
              <option value="asap">Как можно быстрее (ASAP)</option>
            </select>
          </Field>
          <div className="grid grid-cols-2 gap-3 sm:col-span-2">
            <Field label="Дата старта" hint="Пусто — сразу">
              <input type="date" value={startDate} max={endDate || undefined} onChange={(e) => setStartDate(e.target.value)} className={inputCls} />
            </Field>
            <Field label="Дата окончания" hint="Пусто — без ограничения">
              <input type="date" value={endDate} min={startDate || undefined} onChange={(e) => setEndDate(e.target.value)} className={inputCls} />
            </Field>
          </div>
        </div>

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
                className="text-xs font-medium text-accent hover:underline"
              >
                + Добавить окно
              </button>
            ) : null}
          </div>
          {freqCaps.length === 0 ? <p className="text-xs text-muted">Без лимита</p> : null}
          {freqCaps.map((w, i) => (
            <div key={i} className="flex max-w-md items-end gap-2">
              <div className="flex-1">
                <label className="mb-1 block text-xs text-muted">Показов</label>
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
                <label className="mb-1 block text-xs text-muted">Период</label>
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
              <Button variant="ghost" onClick={() => setFreqCaps(freqCaps.filter((_, j) => j !== i))}>
                Удалить
              </Button>
            </div>
          ))}
        </div>

        <label className="flex cursor-pointer items-start gap-2.5">
          <input
            type="checkbox"
            checked={bidShading}
            onChange={(e) => setBidShading(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-border accent-current"
          />
          <span className="flex items-center gap-1.5 text-sm text-foreground">
            Bid-шейдинг
            <HelpTip text="Автоматически снижает ставку ближе к цене клиринга (first-price). Экономия 15–30% без потери объёма." />
          </span>
        </label>

        <label className="flex cursor-pointer items-start gap-2.5">
          <input
            type="checkbox"
            checked={cleanTraffic}
            onChange={(e) => setCleanTraffic(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-border accent-current"
          />
          <span className="flex items-center gap-1.5 text-sm text-foreground">
            Только чистый трафик
            <HelpTip text="Не закупать домены с низким скорингом качества (скликивание, нулевая вовлечённость, аномальный CTR). Скоринг пересчитывается ежедневно; охват может снизиться." />
          </span>
        </label>

        <Field
          label="Профиль скоринга площадок"
          hint="Пользовательские пороги качества (CTR, viewability, поведение, CPA). В режиме «Блокировать» исключает слабый инвентарь."
        >
          <select value={scoringProfileId} onChange={(e) => setScoringProfileId(e.target.value)} className={inputCls}>
            <option value="">Без профиля</option>
            {(scoringProfiles || []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.enforce ? " (блокировка)" : " (наблюдение)"}
              </option>
            ))}
          </select>
        </Field>

        <NotifyEmailsField
          value={notifyEmails}
          onChange={setNotifyEmails}
          fallbackHint={
            notifyInherit
              ? "Если список пуст — письма получают адреса рекламодателя, а если и их нет — владелец аккаунта."
              : "Список пуст и наследование выключено — письма по этой кампании не отправляются (уведомления в кабинете остаются)."
          }
        />
        <label className="flex cursor-pointer items-start gap-2.5">
          <input
            type="checkbox"
            checked={notifyInherit}
            onChange={(e) => setNotifyInherit(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-border accent-current"
          />
          <span className="flex items-center gap-1.5 text-sm text-foreground">
            Наследовать адреса рекламодателя
            <HelpTip text="При пустом списке кампании письма уходят на адреса рекламодателя или владельца. Снимите галочку, чтобы по этой кампании писем не было совсем." />
          </span>
        </label>

        {error ? <p className="text-xs text-danger">{error}</p> : null}
        <div className="flex items-center gap-3">
          <Button onClick={save} disabled={saving}>
            {saving ? "Сохранение…" : "Сохранить настройки"}
          </Button>
          {saved ? <span className="font-mono text-[11px] text-success">Сохранено</span> : null}
        </div>
      </div>
    </Card>
  )
}

/* ---------- Оптимизация закупки ---------- */

function OptimizationCard({ campaign }: { campaign: Campaign }) {
  const { data: pixels } = useSWR<PixelRow[]>("/pixels", fetcher)
  const { data: blocks } = useSWR<BlockRow[]>(`/campaigns/${campaign.id}/blocks`, fetcher)
  const c = campaign as Campaign & {
    pixel_id?: string | null
    goal_id?: string | null
    target_cpa?: number | null
    target_cpc?: number | null
    target_cpm?: number | null
    target_cpv?: number | null
    auto_optimize?: boolean
  }
  const [pixelId, setPixelId] = useState(c.pixel_id || "")
  const { data: goals } = useSWR<GoalRow[]>(pixelId ? `/pixels/${pixelId}/goals` : null, fetcher)
  const [goalId, setGoalId] = useState(c.goal_id || "")
  const [cpa, setCpa] = useState(c.target_cpa ? String(c.target_cpa) : "")
  const [cpc, setCpc] = useState(c.target_cpc ? String(c.target_cpc) : "")
  const [cpm, setCpm] = useState(c.target_cpm ? String(c.target_cpm) : "")
  const [cpv, setCpv] = useState(c.target_cpv ? String(c.target_cpv) : "")
  const [auto, setAuto] = useState(!!c.auto_optimize)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const saveOpt = async () => {
    setSaving(true)
    setSaved(false)
    try {
      await api(`/campaigns/${campaign.id}`, {
        method: "PATCH",
        body: {
          pixel_id: pixelId || null,
          goal_id: goalId || null,
          target_cpa: cpa ? Number(cpa) : null,
          target_cpc: cpc ? Number(cpc) : null,
          target_cpm: cpm ? Number(cpm) : null,
          target_cpv: cpv ? Number(cpv) : null,
          auto_optimize: auto,
        },
      })
      await mutate(`/campaigns/${campaign.id}`)
      setSaved(true)
    } finally {
      setSaving(false)
    }
  }

  const unblock = async (blockId: string) => {
    await api(`/campaigns/${campaign.id}/blocks/${blockId}`, { method: "DELETE" })
    await mutate(`/campaigns/${campaign.id}/blocks`)
  }

  const BLOCK_TYPE_LABELS: Record<string, string> = {
    domain: "Домен",
    supply_partner: "SSP",
    device_type: "Устройство",
    geo_country: "Гео",
    os: "ОС",
    browser: "Браузер",
    ad_type: "Формат",
    creative: "Креатив",
    line_item: "Строка закупки",
    hour: "Час (UTC)",
  }

  return (
    <Card>
      <CardHeader
        title="Оптимизация закупки"
        subtitle="Пиксель, цель и автоматическая отсечка слабых источников"
      />
      <div className="space-y-4 px-6 py-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Пиксель" hint={pixels && pixels.length === 0 ? "Создайте пиксель на странице «Пиксель и цели»" : "Куда придут конверсии"}>
            <select value={pixelId} onChange={(e) => { setPixelId(e.target.value); setGoalId("") }} className={inputCls}>
              <option value="">Без пикселя</option>
              {(pixels || []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.domain})
                </option>
              ))}
            </select>
          </Field>
          <Field label="Цель" hint="Конверсия для оптимизации">
            <select value={goalId} onChange={(e) => setGoalId(e.target.value)} className={inputCls} disabled={!pixelId}>
              <option value="">Все конверсии пикселя</option>
              {(goals || []).map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Целевой CPA, ₽" hint="Максимальная цена конверсии">
            <input type="number" min="0" step="0.01" value={cpa} onChange={(e) => setCpa(e.target.value)} className={inputCls} placeholder="Не задан" />
          </Field>
          <Field label="Целевой CPC, ₽" hint="Максимальная цена клика">
            <input type="number" min="0" step="0.001" value={cpc} onChange={(e) => setCpc(e.target.value)} className={inputCls} placeholder="Не задан" />
          </Field>
          <Field label="Целевой CPM, ₽" hint="Максимальная цена 1000 показов">
            <input type="number" min="0" step="0.01" value={cpm} onChange={(e) => setCpm(e.target.value)} className={inputCls} placeholder="Не задан" />
          </Field>
          <Field label="Целевой CPV, ₽" hint="Максимальная цена досмотра видео">
            <input type="number" min="0" step="0.001" value={cpv} onChange={(e) => setCpv(e.target.value)} className={inputCls} placeholder="Не задан" />
          </Field>
        </div>
        <label className="flex cursor-pointer items-center gap-2.5">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} className="h-4 w-4 rounded border-border accent-current" />
          <span className="text-sm text-foreground">Автооптимизация</span>
          <HelpTip text="Ежечасно блокировать площадки со скликиванием, нулевой вовлечённостью и дорогими кликами/конверсиями." />
        </label>
        <div className="flex items-center gap-3">
          <Button onClick={saveOpt} disabled={saving}>
            {saving ? "Сохранение…" : "Сохранить оптимизацию"}
          </Button>
          {saved ? <span className="font-mono text-[11px] text-success">Сохранено</span> : null}
        </div>

        {blocks && blocks.length > 0 ? (
          <div className="border-t border-border pt-4">
            <p className="mb-2 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
              Заблокировано оптимизатором ({blocks.length})
            </p>
            <div className="space-y-2">
              {blocks.map((b) => (
                <div key={b.id} className="flex flex-wrap items-center justify-between gap-2 rounded-2xl bg-panel px-4 py-2.5">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-foreground">
                      {BLOCK_TYPE_LABELS[b.block_type] || b.block_type}: {b.partner_name || b.block_value}
                    </p>
                    <p className="mt-0.5 text-xs text-muted">{b.reason}</p>
                  </div>
                  <Button variant="ghost" onClick={() => unblock(b.id)}>
                    Разблокировать
                  </Button>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </Card>
  )
}

function targetingSummary(t: Targeting): string {
  const parts = [
    t.geo_countries?.join(", "),
    t.device_types?.map((d) => DEVICES.find((x) => x.value === d)?.label || d).join(", "),
    t.iab_categories?.length ? `IAB: ${t.iab_categories.length}` : undefined,
    t.age_min || t.age_max ? `Возраст ${t.age_min ?? "?"}–${t.age_max ?? "?"}` : undefined,
    t.hours?.length ? `Часы: ${t.hours.length}` : undefined,
  ]
  return parts.filter(Boolean).join(" · ") || "Без ограничений"
}

/* ---------- Диагностика показов ---------- */

type DiagCheck = {
  code: string
  ok: boolean
  severity: "blocker" | "warning"
  title: string
  detail: string
}
type Diagnostics = {
  eligible: boolean
  summary: string
  blocker_count: number
  warning_count: number
  checks: DiagCheck[]
  /** Блокеры запуска (без учёта статуса самой кампании) — то, что вернёт 409 при активации */
  launch_blockers?: DiagCheck[]
  launch_blocker_count?: number
  ready_to_launch?: boolean
}

/** Куда вести пользователя по коду проверки диагностики */
const CHECK_TAB: Record<string, TabKey> = {
  line_items: "groups",
  creatives: "creatives",
  ord_marking: "creatives",
  ord_chain: "settings",
  ad_market_geo: "groups",
  schedule: "settings",
  balance: "settings",
  daily_budget: "settings",
  total_budget: "settings",
  advertiser: "settings",
  scoring: "advanced",
}

type TabKey = "settings" | "groups" | "creatives" | "stats" | "advanced"

/* ---------- Готовность к запуску ---------- */

function ReadinessPanel({
  campaignId,
  campaignStatus,
  onGoTo,
}: {
  campaignId: string
  campaignStatus: string
  onGoTo: (tab: TabKey) => void
}) {
  const { data, error, isLoading } = useSWR<Diagnostics>(`/campaigns/${campaignId}/diagnostics`, fetcher, {
    refreshInterval: 30000,
  })
  if (isLoading) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-border bg-surface px-4 py-3 text-sm text-muted">
        <Spinner /> Проверяем готовность к запуску…
      </div>
    )
  }
  if (error || !data) {
    return (
      <p className="rounded-md border border-border bg-surface px-4 py-3 text-sm text-muted">
        Не удалось проверить готовность — {error instanceof Error ? error.message : "попробуйте обновить страницу"}.
      </p>
    )
  }
  const blockers = data.launch_blockers ?? data.checks.filter((c) => !c.ok && c.severity === "blocker" && c.code !== "campaign_status")
  const warnings = data.checks.filter((c) => !c.ok && c.severity === "warning")
  const active = campaignStatus === "active"

  if (blockers.length === 0 && warnings.length === 0) {
    return (
      <div className="flex items-center gap-2.5 rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="shrink-0">
          <path d="M20 6L9 17l-5-5" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {active ? "Кампания участвует в аукционах — все проверки пройдены." : "Готово к запуску — все проверки пройдены."}
      </div>
    )
  }

  return (
    <div
      role={blockers.length ? "alert" : "status"}
      className={`rounded-md border px-4 py-3 ${
        blockers.length ? "border-red-200 bg-red-50" : "border-amber-200 bg-amber-50"
      }`}
    >
      <p className={`text-sm font-medium ${blockers.length ? "text-red-800" : "text-amber-800"}`}>
        {blockers.length
          ? active
            ? `Кампания активна, но не может показываться: ${blockers.length} ${plural(blockers.length, "проблема", "проблемы", "проблем")}`
            : `Нельзя запустить: ${blockers.length} ${plural(blockers.length, "проблема", "проблемы", "проблем")}`
          : `${warnings.length} ${plural(warnings.length, "предупреждение", "предупреждения", "предупреждений")}`}
      </p>
      <ul className="mt-2 flex flex-col gap-1.5">
        {[...blockers, ...warnings].map((c) => {
          const isBlocker = c.severity === "blocker"
          const target = CHECK_TAB[c.code]
          return (
            <li key={c.code} className="flex flex-wrap items-baseline gap-x-2 text-sm">
              <span className={`font-medium ${isBlocker ? "text-red-800" : "text-amber-800"}`}>{c.title}</span>
              <span className={isBlocker ? "text-red-700/80" : "text-amber-700/80"}>{c.detail}</span>
              {target ? (
                <button
                  type="button"
                  onClick={() => onGoTo(target)}
                  className={`text-xs font-medium underline-offset-2 hover:underline ${isBlocker ? "text-red-800" : "text-amber-800"}`}
                >
                  Исправить →
                </button>
              ) : null}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function plural(n: number, one: string, few: string, many: string) {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few
  return many
}

function DiagnosticsCard({ campaignId, campaignStatus }: { campaignId: string; campaignStatus: string }) {
  const { data, error, isLoading } = useSWR<Diagnostics>(`/campaigns/${campaignId}/diagnostics`, fetcher, {
    refreshInterval: 30000,
  })
  const [expanded, setExpanded] = useState(campaignStatus !== "active")

  const CheckIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="shrink-0 text-emerald-600">
      <path d="M20 6L9 17l-5-5" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
  const FailIcon = ({ blocker }: { blocker: boolean }) => (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      className={`shrink-0 ${blocker ? "text-danger" : "text-amber-500"}`}
    >
      {blocker ? (
        <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
      ) : (
        <path d="M12 8v5m0 3h.01M10.3 3.9L2.4 18a1.9 1.9 0 001.7 2.9h15.8a1.9 1.9 0 001.7-2.9L13.7 3.9a1.9 1.9 0 00-3.4 0z" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      )}
    </svg>
  )

  return (
    <Card>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-6 py-4 text-left"
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-2.5">
          <h2 className="text-sm font-semibold text-foreground">Диагностика показов</h2>
          {data ? (
            data.eligible && data.blocker_count === 0 && data.warning_count === 0 ? (
              <span className="rounded-md bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">Всё в порядке</span>
            ) : data.blocker_count > 0 ? (
              <span className="rounded-md bg-red-50 px-2 py-0.5 text-[11px] font-medium text-red-700">
                {data.blocker_count} проблем(а)
              </span>
            ) : (
              <span className="rounded-md bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                {data.warning_count} предупреждени(я)
              </span>
            )
          ) : null}
        </div>
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          aria-hidden="true"
          className={`text-muted transition-transform ${expanded ? "rotate-180" : ""}`}
        >
          <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {expanded ? (
        <div className="border-t border-border px-6 py-4">
          {isLoading ? (
            <Spinner />
          ) : error ? (
            <p className="text-sm text-muted">
              {error instanceof Error ? error.message : "Не удалось загрузить диагностику."}
            </p>
          ) : data ? (
            <>
              <p className="mb-3 text-sm text-muted">{data.summary}</p>
              <ul className="space-y-2">
                {data.checks.map((c) => (
                  <li key={c.code} className="flex items-start gap-2.5">
                    {c.ok ? <CheckIcon /> : <FailIcon blocker={c.severity === "blocker"} />}
                    <div>
                      <p className={`text-sm font-medium ${c.ok ? "text-foreground" : c.severity === "blocker" ? "text-danger" : "text-amber-600"}`}>
                        {c.title}
                      </p>
                      <p className="text-xs text-muted">{c.detail}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}
    </Card>
  )
}

/* ---------- Страница ---------- */

export default function CampaignDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const { data: campaign, error: campaignError, isLoading: campaignLoading } = useSWR<CampaignExt>(`/campaigns/${id}`, fetcher)
  const { data: lineItems } = useSWR<LineItem[]>(`/line-items?campaign_id=${id}`, fetcher)
  const { data: stats } = useSWR<StatsSummary>(`/stats/summary?hours=168&campaign_id=${id}`, fetcher)
  const { data: campaignCreatives } = useSWR<Creative[]>(`/creatives?campaign_id=${id}`, fetcher)
  // ТЗ п.5: кампании рекламодателя «за пределами РФ» — без ОРД и без российских гео.
  const { data: advertisers } = useAdvertisers()
  const foreign = isForeignAdvertiser(advertisers, campaign?.advertiser_id)

  const [open, setOpen] = useState(false)
  const [editingLi, setEditingLi] = useState<LineItem | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [targeting, setTargeting] = useState<Targeting>({})
  // G9: креативы, привязанные к line item (пусто = все креативы кампании)
  const [liCreativeIds, setLiCreativeIds] = useState<string[]>([])
  const [form, setForm] = useState({
    name: "",
    bid_type: "cpm",
    bid_price: "1.5",
    max_bid: "",
    daily_budget: "20",
  })
  const [statusError, setStatusError] = useState<string | null>(null)

  const openCreate = () => {
    setError(null)
    setEditingLi(null)
    setForm({ name: "", bid_type: "cpm", bid_price: "1.5", max_bid: "", daily_budget: "20" })
    setTargeting({})
    setLiCreativeIds([])
    setOpen(true)
  }

  const openEdit = (li: LineItem) => {
    setError(null)
    setEditingLi(li)
    setForm({
      name: li.name,
      bid_type: li.bid_type,
      bid_price: String(li.bid_price),
      max_bid: li.max_bid ? String(li.max_bid) : "",
      daily_budget: String(li.daily_budget),
    })
    setTargeting(li.targeting || {})
    setLiCreativeIds((campaignCreatives || []).filter((c) => c.line_item_id === li.id).map((c) => c.id))
    setOpen(true)
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const body = {
        name: form.name,
        bid_type: form.bid_type,
        bid_price: Number(form.bid_price),
        max_bid: form.max_bid ? Number(form.max_bid) : null,
        daily_budget: Number(form.daily_budget),
        targeting,
      }
      let liId = editingLi?.id
      if (editingLi) {
        await api(`/line-items/${editingLi.id}`, { method: "PATCH", body })
      } else {
        const created = await api<LineItem>(`/line-items`, { method: "POST", body: { campaign_id: id, ...body } })
        liId = created.id
      }
      // G9: сохраняем привязку креативов (пустой список = все креативы кампании)
      if (liId) {
        await api(`/line-items/${liId}/creatives`, { method: "PUT", body: { creative_ids: liCreativeIds } })
      }
      await mutate(`/line-items?campaign_id=${id}`)
      await mutate(`/creatives?campaign_id=${id}`)
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка сохранения")
    } finally {
      setSaving(false)
    }
  }

  const toggleLi = async (li: LineItem) => {
    await api(`/line-items/${li.id}`, {
      method: "PATCH",
      body: { status: li.status === "active" ? "paused" : "active" },
    })
    await mutate(`/line-items?campaign_id=${id}`)
  }

  const [toggling, setToggling] = useState(false)
  const toggleCampaign = async () => {
    if (!campaign || toggling) return
    setStatusError(null)
    setToggling(true)
    const activating = campaign.status !== "active"
    try {
      await api(`/campaigns/${id}`, {
        method: "PATCH",
        body: { status: activating ? "active" : "paused" },
      })
      await Promise.all([mutate(`/campaigns/${id}`), mutate(`/campaigns/${id}/diagnostics`)])
      toastSuccess(activating ? "Кампания запущена" : "Кампания на паузе")
    } catch (err) {
      // 409 от гейта запуска: список блокеров уже покажет ReadinessPanel — обновляем диагностику
      if (err instanceof ApiError && err.status === 409) {
        await mutate(`/campaigns/${id}/diagnostics`)
        setStatusError(err.message)
      } else {
        setStatusError(err instanceof Error ? err.message : "Не удалось изменить статус")
      }
    } finally {
      setToggling(false)
    }
  }

  // Вкладки карточки: Настройки → Группы объявлений → Креативы → Статистика → Продвинутое.
  // «Группы объявлений» = line_items в БД (таргетинг + ставка, их читает биддер).
  const [tab, setTab] = useState<TabKey>("settings")

  // H1: «Создать похожую» — копия кампании + line items + креативы (draft)
  const router = useRouter()
  const [duplicating, setDuplicating] = useState(false)
  const duplicateCampaign = async () => {
    if (!campaign || duplicating) return
    setDuplicating(true)
    try {
      const copy = await api<Campaign>(`/campaigns/${id}/duplicate`, { method: "POST" })
      toastSuccess("Копия создана — креативы отправятся на модерацию заново")
      await mutate("/campaigns")
      router.push(`/campaigns/${copy.id}`)
    } catch {
      // ошибка уже показана toast-обработчиком api()
    } finally {
      setDuplicating(false)
    }
  }

  // Ошибка загрузки (404/500/сеть) — показываем состояние вместо вечного спиннера
  if (campaignError) {
    const notFound = campaignError instanceof ApiError && campaignError.status === 404
    return (
      <div className="space-y-4">
        <Link href="/campaigns" className="text-xs text-muted hover:text-accent">
          ← Все кампании
        </Link>
        <EmptyState
          title={notFound ? "Кампания не найдена" : "Не удалось загрузить кампанию"}
          description={
            notFound
              ? "Возможно, кампания была удалена или у вас нет к ней доступа."
              : campaignError instanceof Error
                ? campaignError.message
                : "Попробуйте обновить страницу."
          }
          action={
            <Button onClick={() => mutate(`/campaigns/${id}`)}>Повторить</Button>
          }
        />
      </div>
    )
  }
  if (campaignLoading || !campaign) return <Spinner />

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/campaigns" className="text-xs text-muted hover:text-accent">
            ← Все кампании
          </Link>
          <div className="mt-1 flex flex-wrap items-center gap-3">
            <h1 className="text-xl font-semibold text-foreground">{campaign.name}</h1>
            <CopyId value={campaign.display_id} label={`ID: ${campaign.display_id}`} />
            <StatusBadge status={campaign.status} />
          </div>
          <p className="mt-0.5 text-sm text-muted">
            Дневной бюджет {fmtMoney(campaign.daily_budget)} · Общий {fmtMoney(campaign.total_budget)} · Потрачено{" "}
            {fmtMoney(campaign.spent_total)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={duplicateCampaign} disabled={duplicating}>
            {duplicating ? "Копирование…" : "Создать похожую"}
          </Button>
          <Button
            variant={campaign.status === "active" ? "secondary" : "primary"}
            onClick={toggleCampaign}
            disabled={toggling}
          >
            {toggling ? "…" : campaign.status === "active" ? "Пауза" : "Запустить"}
          </Button>
        </div>
      </div>

      {statusError ? (
        <p role="alert" className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
          {statusError}
        </p>
      ) : null}

      {/* Готовность к запуску: те же проверки, что использует бэкенд-гейт активации и биддер */}
      <ReadinessPanel campaignId={id} campaignStatus={campaign.status} onGoTo={setTab} />

      {/* Вкладки карточки кампании: путь запуска слева направо */}
      <div role="tablist" aria-label="Разделы кампании" className="flex gap-1 overflow-x-auto border-b border-border">
        {(
          [
            ["settings", "Настройки"],
            ["groups", `Группы объявлений${lineItems ? ` · ${lineItems.length}` : ""}`],
            ["creatives", `Креативы${campaignCreatives ? ` · ${campaignCreatives.length}` : ""}`],
            ["stats", "Статистика"],
            ["advanced", "Продвинутое"],
          ] as [TabKey, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`-mb-px shrink-0 rounded-t-md px-4 py-2 text-sm font-medium transition-colors ${
              tab === key
                ? "border-x border-t border-border bg-card text-foreground"
                : "text-muted hover:text-foreground"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "settings" ? (
        <>
          {foreign ? null : (
          <Card>
            <CardHeader
              title="Данные ОРД"
              subtitle="Рекламодатель и договоры, на которые регистрируются erid креативов. Без подтверждения кампания не запустится."
            />
            <OrdChainCampaignCard
              campaignId={campaign.id}
              canEdit={!campaign.ord_confirmed_at}
              values={{
                ord_mode: campaign.ord_mode ?? null,
                counterparty_id: campaign.counterparty_id ?? null,
                initial_contract_id: campaign.initial_contract_id ?? null,
                executive_contract_id: campaign.executive_contract_id ?? null,
              }}
              onValuesChange={async (v: OrdChainValues) => {
                await api(`/campaigns/${campaign.id}`, { method: "PATCH", body: v })
                await mutate(`/campaigns/${campaign.id}`)
              }}
            />
          </Card>
          )}
          <SettingsCard campaign={campaign} />
        </>
      ) : null}

      {tab === "groups" ? (
        <Card>
          <CardHeader
            title="Группы объявлений"
            subtitle="Ставка, бюджет и таргетинг группы — именно их использует биддер в аукционе. Креативы привязываются к группе."
            action={<Button onClick={openCreate}>Добавить группу</Button>}
          />
          {lineItems ? (
            lineItems.length > 0 ? (
              <Table headers={["Название", "Статус", "Ставка", "Дневной бюджет", "Расход", "Таргетинг", "Креативы", ""]}>
                {lineItems.map((li) => {
                  const bound = (campaignCreatives || []).filter((c) => c.line_item_id === li.id).length
                  return (
                    <tr key={li.id} className="hover:bg-surface">
                      <td className="px-4 py-3 font-medium text-foreground">{li.name}</td>
                      <td className="px-4 py-3">
                        <StatusBadge status={li.status} />
                      </td>
                      <td className="px-4 py-3 font-mono text-xs">
                        {fmtMoney(li.bid_price)} {li.bid_type.toUpperCase()}
                      </td>
                      <td className="px-4 py-3 font-mono text-xs">{fmtMoney(li.daily_budget)}</td>
                      <td className="px-4 py-3 font-mono text-xs">{fmtMoney(li.spent_today)}</td>
                      <td className="max-w-[280px] px-4 py-3 text-xs text-muted">{targetingSummary(li.targeting)}</td>
                      <td className="px-4 py-3 text-xs text-muted">
                        {bound > 0 ? `${bound} выбрано` : "все креативы кампании"}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-1.5">
                          <Button variant="ghost" onClick={() => openEdit(li)}>
                            Изменить
                          </Button>
                          <Button variant="ghost" onClick={() => toggleLi(li)}>
                            {li.status === "active" ? "Пауза" : "Запустить"}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </Table>
            ) : (
              <EmptyState
                title="Нет групп объявлений"
                description="Группа задаёт ставку и таргетинг — без неё кампания не участвует в аукционах."
                action={<Button onClick={openCreate}>Добавить группу</Button>}
              />
            )
          ) : (
            <Spinner />
          )}
        </Card>
      ) : null}

      {tab === "creatives" ? (
        <Card>
          <CardHeader
            title="Креативы"
            subtitle="Объявления этой кампании. Показываются только одобренные модерацией; для трафика РФ нужна маркировка (erid)."
            action={
              <Button href={`/creatives?campaign_id=${id}`}>Добавить / управлять</Button>
            }
          />
          {campaignCreatives ? (
            campaignCreatives.length > 0 ? (
              <div className="grid gap-3 px-6 pb-4 sm:grid-cols-2 lg:grid-cols-3">
                {campaignCreatives.map((c) => {
                  const group = lineItems?.find((li) => li.id === c.line_item_id)
                  return (
                    <div key={c.id} className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-2">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-foreground">{c.name}</p>
                        <p className="truncate text-xs text-muted">
                          {c.type === "banner" ? `Баннер ${c.width}×${c.height}` : c.type === "video" ? "Видео" : "Native"}
                          {" · "}
                          {group ? group.name : "все группы"}
                          {c.erid ? "" : " · без erid"}
                        </p>
                      </div>
                      <StatusBadge status={c.status} />
                    </div>
                  )
                })}
              </div>
            ) : (
              <EmptyState
                title="Нет креативов"
                description="Добавьте хотя бы один креатив, чтобы кампания могла показываться."
                action={
                  <Button href={`/creatives?campaign_id=${id}`}>Добавить креатив</Button>
                }
              />
            )
          ) : (
            <Spinner />
          )}
        </Card>
      ) : null}

      {tab === "stats" ? (
        <>
          {stats ? (
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <KpiCard label="Расход (7 дней)" value={fmtMoney(stats.spend)} />
              <KpiCard label="Показы" value={fmtNum(stats.impressions)} />
              <KpiCard label="Клики" value={fmtNum(stats.clicks)} hint={`CTR ${fmtPct(stats.ctr)}`} />
              <KpiCard label="Win Rate" value={fmtPct(stats.win_rate)} />
            </div>
          ) : (
            <Spinner />
          )}
          <div className="flex justify-end">
            <Link href={`/analytics?campaign_id=${id}`} className="text-sm font-medium text-accent hover:underline">
              Подробная аналитика →
            </Link>
          </div>
          <DiagnosticsCard campaignId={id} campaignStatus={campaign.status} />
        </>
      ) : null}

      {tab === "advanced" ? <OptimizationCard campaign={campaign} /> : null}

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={editingLi ? `Группа объявлений: ${editingLi.name}` : "Новая группа объявлений"}
        wide
      >
        <form onSubmit={save} className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Название">
              <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={inputCls} placeholder="RU мобайл баннеры" />
            </Field>
            <Field label="Тип ставки">
              <select value={form.bid_type} onChange={(e) => setForm({ ...form, bid_type: e.target.value })} className={inputCls}>
                <option value="cpm">CPM (за 1000 показов)</option>
                <option value="cpc">CPC (за клик)</option>
              </select>
            </Field>
            <Field
              label="Ставка, ₽"
              hint={
                Number(form.bid_price) > 0 && Number(form.bid_price) < 0.5
                  ? "Низкая ставка: у большинства SSP bid floor выше — показов может не быть совсем"
                  : "Ставка должна быть выше bid floor площадок, иначе биддер проигрывает аукционы"
              }
            >
              <input type="number" required min="0.01" step="0.01" value={form.bid_price} onChange={(e) => setForm({ ...form, bid_price: e.target.value })} className={inputCls} />
            </Field>
            <Field label="Макс. ставка, ₽" hint="Для автооптимизатора">
              <input type="number" min="0.01" step="0.01" value={form.max_bid} onChange={(e) => setForm({ ...form, max_bid: e.target.value })} className={inputCls} />
            </Field>
            <Field label="Дневной бюджет, ₽">
              <input type="number" required min="0.01" step="0.01" value={form.daily_budget} onChange={(e) => setForm({ ...form, daily_budget: e.target.value })} className={inputCls} />
            </Field>
          </div>

          <TargetingEditor value={targeting} onChange={setTargeting} foreignOnly={foreign} />

          {/* G9: привязка креативов к строке закупки */}
          <div className="rounded-md border border-border bg-surface p-3">
            <p className="text-xs font-medium text-foreground">Креативы этой группы</p>
            <p className="mb-2 mt-0.5 text-[11px] text-muted">
              Если ничего не выбрано — крутятся все креативы кампании. Выберите конкретные, чтобы ограничить.
            </p>
            {campaignCreatives && campaignCreatives.length > 0 ? (
              <div className="flex max-h-40 flex-col gap-1 overflow-y-auto">
                {campaignCreatives.map((c) => (
                  <label key={c.id} className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-xs hover:bg-background">
                    <input
                      type="checkbox"
                      checked={liCreativeIds.includes(c.id)}
                      onChange={(e) =>
                        setLiCreativeIds((prev) =>
                          e.target.checked ? [...prev, c.id] : prev.filter((x) => x !== c.id),
                        )
                      }
                      className="h-3.5 w-3.5 accent-accent"
                    />
                    <span className="truncate text-foreground">{c.name}</span>
                    <span className="ml-auto shrink-0 font-mono text-[10px] text-muted">
                      {c.width && c.height ? `${c.width}×${c.height}` : c.type}
                    </span>
                    <StatusBadge status={c.status} />
                  </label>
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted">В кампании пока нет креативов — добавьте их в разделе «Креативы».</p>
            )}
          </div>

          {error ? <p className="text-xs text-danger">{error}</p> : null}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? "Сохранение…" : editingLi ? "Сохранить" : "Создать"}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  )
}
