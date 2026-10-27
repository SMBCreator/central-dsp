"use client"

import { useState } from "react"
import useSWR from "swr"
import { api, fetcher } from "@/lib/api"
import type { PublicSettings } from "@/lib/types"
import { Button, Card, CardHeader, Field, Modal, EmptyState, Spinner, inputCls } from "@/components/ui"
import { ConfirmDialog } from "@/components/confirm-dialog"

interface Pixel {
  id: string
  name: string
  domain: string
  goals_count: number
  sessions_24h: number
  conversions_24h: number
  created_at: string
}

interface Goal {
  id: string
  name: string
  goal_type: string
  match_value: string
  value: number
  conversions_total: number
}

interface PixelStats {
  sessions: number
  pageviews: number
  avg_session_sec: number
  ad_sessions: number
  conversions: number
  avg_depth?: number
  bounce_rate?: number
  avg_scroll_pct?: number
  by_source: { source: string; conversions: number; value: number }[]
}

const GOAL_TYPES = [
  { value: "event", label: "Событие (cdsp.track)", hint: "Напр.: purchase, lead, signup" },
  { value: "url", label: "Посещение URL", hint: "Подстрока URL, напр.: /thank-you" },
  { value: "duration", label: "Время на сайте", hint: "Секунды, напр.: 60" },
]

export default function PixelsPage() {
  const { data: pixels, mutate } = useSWR<Pixel[]>("/pixels", fetcher)
  const { data: publicSettings } = useSWR<PublicSettings>("/settings/public", fetcher)
  const [createModal, setCreateModal] = useState(false)
  const [selected, setSelected] = useState<Pixel | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const [pName, setPName] = useState("")
  const [pDomain, setPDomain] = useState("")

  const createPixel = async () => {
    setError(null)
    try {
      await api("/pixels", {
        method: "POST",
        body: { name: pName, domain: pDomain },
      })
      setCreateModal(false)
      setPName("")
      setPDomain("")
      mutate()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка создания")
    }
  }

  // H2: модалка подтверждения вместо confirm()
  const [deletingPixel, setDeletingPixel] = useState<string | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const deletePixel = (id: string) => setDeletingPixel(id)
  const confirmDeletePixel = async () => {
    if (!deletingPixel) return
    setDeleteBusy(true)
    try {
      await api(`/pixels/${deletingPixel}`, { method: "DELETE" })
      if (selected?.id === deletingPixel) setSelected(null)
      setDeletingPixel(null)
      mutate()
    } finally {
      setDeleteBusy(false)
    }
  }

  // Адрес скрипта берём из настроек деплоя (PUBLIC_API_BASE_URL), а не из
  // window.location: за прокси origin кабинета и публичный API могут различаться,
  // и скрипт с неверного адреса шлёт beacon мимо backend (audit E-05).
  const scriptTemplate = publicSettings?.pixel_script_url_template ?? null
  const snippet = (id: string) =>
    scriptTemplate ? `<script async src="${scriptTemplate.replace("{pixel_id}", id)}"></script>` : null

  const copySnippet = (id: string) => {
    const s = snippet(id)
    if (!s) return
    navigator.clipboard.writeText(s)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="space-y-6">
      <ConfirmDialog
        open={deletingPixel !== null}
        title="Удалить пиксель?"
        description="Все события и цели этого пикселя будут удалены безвозвратно."
        busy={deleteBusy}
        onConfirm={confirmDeletePixel}
        onClose={() => setDeletingPixel(null)}
      />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">Central Pixel</h1>
          <p className="mt-1 text-sm text-muted">
            Аналитика конверсий на сайте клиента. Оптимизатор закупки использует эти данные автоматически.
          </p>
        </div>
        <Button onClick={() => setCreateModal(true)}>Создать пиксель</Button>
      </div>

      {!pixels ? (
        <Spinner />
      ) : pixels.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            title="Пикселей пока нет"
            description="Создайте пиксель, установите его на сайт клиента и настройте цели — платформа начнёт оптимизировать закупку по конверсиям."
            action={<Button onClick={() => setCreateModal(true)}>Создать пиксель</Button>}
          />
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {pixels.map((p) => (
            <Card key={p.id} className="flex flex-col p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="font-semibold text-foreground">{p.name}</p>
                  <p className="font-mono text-xs text-muted">{p.domain}</p>
                </div>
                <div className="flex gap-2">
                  <Button variant="secondary" onClick={() => setSelected(p)}>
                    Цели и статистика
                  </Button>
                  <Button variant="ghost" onClick={() => deletePixel(p.id)}>
                    Удалить
                  </Button>
                </div>
              </div>
              <div className="mt-4 grid grid-cols-3 gap-3 border-t border-border pt-4">
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted">Сессии 24ч</p>
                  <p className="mt-1 text-lg font-bold text-foreground">{p.sessions_24h}</p>
                </div>
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted">Конверсии 24ч</p>
                  <p className="mt-1 text-lg font-bold text-foreground">{p.conversions_24h}</p>
                </div>
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted">Целей</p>
                  <p className="mt-1 text-lg font-bold text-foreground">{p.goals_count}</p>
                </div>
              </div>
              <div className="mt-4 rounded-xl bg-panel p-3">
                {snippet(p.id) ? (
                  <div className="flex items-center justify-between gap-2">
                    <code className="truncate font-mono text-[11px] text-muted">{snippet(p.id)}</code>
                    <Button variant="secondary" onClick={() => copySnippet(p.id)}>
                      {copied ? "Скопировано" : "Копировать"}
                    </Button>
                  </div>
                ) : (
                  <p className="text-xs text-warning">
                    Публичный адрес API не настроен (PUBLIC_API_BASE_URL или PUBLIC_BASE_URL на сервере) —
                    код вставки недоступен, скрипт не будет доставлять события.
                  </p>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}

      <Modal open={createModal} onClose={() => setCreateModal(false)} title="Новый пиксель">
        <div className="space-y-4">
          <Field label="Название">
            <input className={inputCls} value={pName} onChange={(e) => setPName(e.target.value)} placeholder="Сайт клиента" />
          </Field>
          <Field label="Домен сайта">
            <input className={inputCls} value={pDomain} onChange={(e) => setPDomain(e.target.value)} placeholder="example.com" />
          </Field>
          {error ? <p className="text-sm text-danger">{error}</p> : null}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setCreateModal(false)}>
              Отмена
            </Button>
            <Button onClick={createPixel} disabled={!pName || !pDomain}>
              Создать
            </Button>
          </div>
        </div>
      </Modal>

      {selected ? <PixelDetail pixel={selected} onClose={() => setSelected(null)} /> : null}

      <SetupGuide />
    </div>
  )
}

function SetupGuide() {
  const [openStep, setOpenStep] = useState<number | null>(null)
  const steps = [
    {
      title: "1. Установите сниппет на сайт",
      body: (
        <div className="space-y-2 text-sm text-muted">
          <p>
            Скопируйте сниппет пикселя (кнопка «Копировать» на карточке) и вставьте его в HTML сайта — в конец блока{" "}
            <code className="rounded bg-panel px-1 font-mono text-xs">{"<head>"}</code> или перед закрывающим{" "}
            <code className="rounded bg-panel px-1 font-mono text-xs">{"</body>"}</code> на всех страницах. В Tilda,
            WordPress и Bitrix — через раздел «Вставка HTML-кода» / «Счётчики».
          </p>
          <p>
            Скрипт лёгкий и асинхронный: он не блокирует загрузку страницы. Сразу после установки пиксель начинает
            считать сессии, время на сайте, глубину просмотра и прокрутки.
          </p>
        </div>
      ),
    },
    {
      title: "2. Свяжите рекламу с сайтом (клик → визит)",
      body: (
        <div className="space-y-2 text-sm text-muted">
          <p>
            К клику по вашему объявлению платформа автоматически добавляет метку{" "}
            <code className="rounded bg-panel px-1 font-mono text-xs">?cdsp_clid=…</code> в URL посадочной страницы.
            Пиксель сам считывает метку и привязывает все дальнейшие действия посетителя к конкретному показу: кампании,
            креативу, площадке и SSP.
          </p>
          <p>Ничего настраивать не нужно — важно лишь не обрезать параметры URL на посадочной странице (редиректы, чистка UTM).</p>
        </div>
      ),
    },
    {
      title: "3. Настройте цели",
      body: (
        <div className="space-y-2 text-sm text-muted">
          <p>Откройте «Цели и статистика» на карточке пикселя и добавьте цели. Три типа:</p>
          <ul className="list-inside list-disc space-y-1.5">
            <li>
              <span className="font-medium text-foreground">Событие</span> — вызовите{" "}
              <code className="rounded bg-panel px-1 font-mono text-xs">{'window.cdsp.track("purchase")'}</code> в коде
              сайта в момент действия (оформление заказа, отправка формы, клик по кнопке). В условии цели укажите то же
              имя: <code className="rounded bg-panel px-1 font-mono text-xs">purchase</code>.
            </li>
            <li>
              <span className="font-medium text-foreground">Посещение URL</span> — цель засчитывается, когда посетитель
              открывает страницу, содержащую подстроку. Например{" "}
              <code className="rounded bg-panel px-1 font-mono text-xs">/thank-you</code> — страница «Спасибо за заказ».
              Кода не требуется.
            </li>
            <li>
              <span className="font-medium text-foreground">Время на сайте</span> — цель засчитывается, когда посетитель
              проводит на сайте суммарно больше N секунд (например, 60). Кода не требуется.
            </li>
          </ul>
          <p>
            Укажите «Ценность конверсии» — и в статистике появится доход и ROI. Одна цель засчитывается один раз за
            сессию.
          </p>
        </div>
      ),
    },
    {
      title: "4. Что дальше: оптимизация и аудитории",
      body: (
        <div className="space-y-2 text-sm text-muted">
          <p>
            Конверсии из пикселя попадают в статистику кампаний (CPA, CVR), в автоправила (например, «снизить ставку при
            дорогом CPA») и в скоринг качества площадок. В «Аудиториях» можно собрать сегменты посетителей (все, по URL,
            по событию, по цели) для ретаргетинга и исключений.
          </p>
          <p>
            Выберите пиксель и цель в настройках кампании — оптимизатор будет автоматически блокировать площадки, которые
            тратят бюджет без конверсий.
          </p>
        </div>
      ),
    },
  ]
  return (
    <Card>
      <CardHeader title="Как установить и настроить" subtitle="Пошаговая инструкция: сниппет, привязка кликов, цели" />
      <div className="divide-y divide-border">
        {steps.map((s, i) => (
          <div key={s.title}>
            <button
              type="button"
              onClick={() => setOpenStep(openStep === i ? null : i)}
              className="flex w-full items-center justify-between px-6 py-4 text-left transition-colors hover:bg-surface"
              aria-expanded={openStep === i}
            >
              <span className="text-sm font-medium text-foreground">{s.title}</span>
              <span className="font-mono text-xs text-muted">{openStep === i ? "−" : "+"}</span>
            </button>
            {openStep === i ? <div className="px-6 pb-4">{s.body}</div> : null}
          </div>
        ))}
      </div>
    </Card>
  )
}

function PixelDetail({ pixel, onClose }: { pixel: Pixel; onClose: () => void }) {
  const { data: goals, mutate: mutateGoals } = useSWR<Goal[]>(`/pixels/${pixel.id}/goals`, fetcher)
  const { data: stats } = useSWR<PixelStats>(`/pixels/${pixel.id}/stats?hours=168`, fetcher)
  const [error, setError] = useState<string | null>(null)

  const [gName, setGName] = useState("")
  const [gType, setGType] = useState("event")
  const [gMatch, setGMatch] = useState("")
  const [gValue, setGValue] = useState("")

  const activeType = GOAL_TYPES.find((t) => t.value === gType)

  const createGoal = async () => {
    setError(null)
    try {
      await api(`/pixels/${pixel.id}/goals`, {
        method: "POST",
        body: {
          name: gName,
          goal_type: gType,
          match_value: gMatch,
          value: gValue ? Number(gValue) : 0,
        },
      })
      setGName("")
      setGMatch("")
      setGValue("")
      mutateGoals()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка")
    }
  }

  const deleteGoal = async (goalId: string) => {
    await api(`/pixels/${pixel.id}/goals/${goalId}`, { method: "DELETE" })
    mutateGoals()
  }

  return (
    <Modal open onClose={onClose} title={`${pixel.name} — цели и статистика`}>
      <div className="max-h-[70vh] space-y-5 overflow-y-auto pr-1">
        {stats ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              { label: "Сессии 7д", value: String(stats.sessions) },
              { label: "Просмотры", value: String(stats.pageviews) },
              { label: "Ср. время", value: `${stats.avg_session_sec}с` },
              { label: "С рекламы", value: String(stats.ad_sessions) },
              { label: "Конверсии", value: String(stats.conversions) },
              { label: "Глубина", value: stats.avg_depth != null ? `${stats.avg_depth} стр.` : "—" },
              { label: "Отказы", value: stats.bounce_rate != null ? `${stats.bounce_rate}%` : "—" },
              { label: "Скролл", value: stats.avg_scroll_pct != null ? `${stats.avg_scroll_pct}%` : "—" },
            ].map((s) => (
              <div key={s.label} className="rounded-xl bg-panel p-3">
                <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">{s.label}</p>
                <p className="mt-1 text-base font-bold text-foreground">{s.value}</p>
              </div>
            ))}
          </div>
        ) : (
          <Spinner />
        )}

        {stats && stats.by_source.length > 0 ? (
          <div>
            <p className="mb-2 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
              Конверсии по источникам
            </p>
            <div className="space-y-1.5">
              {stats.by_source.map((s) => (
                <div key={s.source} className="flex items-center justify-between rounded-lg bg-panel px-3 py-2">
                  <span className="font-mono text-xs text-foreground">{s.source}</span>
                  <span className="font-mono text-xs text-muted">
                    {s.conversions} конв. / {Number(s.value).toLocaleString("ru-RU")} ₽
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <div className="border-t border-border pt-4">
          <p className="mb-3 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">Цели</p>
          {!goals ? (
            <Spinner />
          ) : goals.length === 0 ? (
            <p className="text-sm text-muted">Целей пока нет — добавьте первую ниже.</p>
          ) : (
            <div className="space-y-1.5">
              {goals.map((g) => (
                <div key={g.id} className="flex items-center justify-between rounded-lg bg-panel px-3 py-2">
                  <div>
                    <span className="text-sm font-medium text-foreground">{g.name}</span>
                    <span className="ml-2 font-mono text-[11px] text-muted">
                      {g.goal_type === "event" ? `событие: ${g.match_value}` : g.goal_type === "url" ? `url: ${g.match_value}` : `${g.match_value} сек на сайте`}
                      {Number(g.value) > 0 ? ` · ${Number(g.value).toLocaleString("ru-RU")} ₽` : ""}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-xs text-muted">{g.conversions_total} конв.</span>
                    <button type="button" className="text-xs text-danger hover:underline" onClick={() => deleteGoal(g.id)}>
                      Удалить
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="mt-4 space-y-3 rounded-xl border border-border p-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Название цели">
                <input className={inputCls} value={gName} onChange={(e) => setGName(e.target.value)} placeholder="Покупка" />
              </Field>
              <Field label="Тип">
                <select className={inputCls} value={gType} onChange={(e) => setGType(e.target.value)}>
                  {GOAL_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Условие" hint={activeType?.hint}>
                <input className={inputCls} value={gMatch} onChange={(e) => setGMatch(e.target.value)} placeholder={activeType?.hint} />
              </Field>
              <Field label="Ценность конверсии, ₽" hint="Для расчёта ROI">
                <input type="number" min={0} className={inputCls} value={gValue} onChange={(e) => setGValue(e.target.value)} placeholder="0" />
              </Field>
            </div>
            {error ? <p className="text-sm text-danger">{error}</p> : null}
            <div className="flex justify-end">
              <Button onClick={createGoal} disabled={!gName || !gMatch}>
                Добавить цель
              </Button>
            </div>
          </div>
        </div>
      </div>
    </Modal>
  )
}
