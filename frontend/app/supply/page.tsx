"use client"

import { useMemo, useState } from "react"
import useSWR, { mutate } from "swr"
import { api, ApiError, fetcher } from "@/lib/api"
import { AD_TYPE_LABELS, type PublicSettings, type SspCheckResult, type SupplyPartner } from "@/lib/types"
import { SspPartnerStats } from "@/components/ssp-partner-stats"
import { SspIdSync } from "@/components/ssp-id-sync"
import {
  Button,
  Card,
  CardHeader,
  EmptyState,
  Field,
  HelpTip,
  Modal,
  Spinner,
  StatusBadge,
  inputCls,
} from "@/components/ui"

/**
 * Подключение SSP (онбординг). SuperAdmin заводит партнёра по анкете —
 * требованиям, которые присылает SSP: версия OpenRTB, тип аукциона, валюта,
 * tmax, QPS, форматы, bid floor, IP-whitelist, seat ID, контакт. Платформа
 * выдаёт партнёру bid-эндпоинт и секретный токен, а перед включением
 * прогоняет автопроверку подключения (эндпоинт, auth, парсинг, ответ,
 * макросы, tmax, no-bid). Жизненный цикл: draft → testing → active → paused.
 */

type FormState = {
  name: string
  protocol_version: string
  auction_type: string
  currency: string
  tmax_ms: string
  qps_limit: string
  bid_floor: string
  allowed: string[]
  ip_whitelist: string
  seat_id: string
  contact_email: string
  billing_event: "win" | "burl"
  freq_cap_on_win: boolean
  notes: string
}

const EMPTY_FORM: FormState = {
  name: "",
  protocol_version: "2.5",
  auction_type: "2",
  currency: "RUB",
  tmax_ms: "200",
  qps_limit: "100",
  bid_floor: "0",
  allowed: ["banner", "native", "video", "push"],
  ip_whitelist: "",
  seat_id: "",
  contact_email: "",
  billing_event: "burl",
  freq_cap_on_win: false,
  notes: "",
}

function partnerToForm(p: SupplyPartner): FormState {
  return {
    name: p.name,
    protocol_version: p.protocol_version || "2.5",
    auction_type: String(p.auction_type),
    currency: p.currency || "RUB",
    tmax_ms: String(p.tmax_ms ?? 200),
    qps_limit: String(p.qps_limit),
    bid_floor: String(p.bid_floor),
    allowed: p.allowed_ad_types || [],
    ip_whitelist: (p.ip_whitelist || []).join("\n"),
    seat_id: p.seat_id || "",
    contact_email: p.contact_email || "",
    // партнёры до миграции 046 остаются на win
    billing_event: p.billing_event === "burl" ? "burl" : "win",
    freq_cap_on_win: Boolean(p.freq_cap_on_win),
    notes: p.notes || "",
  }
}

function formToPayload(f: FormState) {
  return {
    name: f.name.trim(),
    protocol_version: f.protocol_version,
    auction_type: Number(f.auction_type),
    currency: f.currency,
    tmax_ms: Number(f.tmax_ms),
    qps_limit: Number(f.qps_limit),
    bid_floor: Number(f.bid_floor),
    allowed_ad_types: f.allowed,
    ip_whitelist: f.ip_whitelist
      .split(/[\n,;]+/)
      .map((s) => s.trim())
      .filter(Boolean),
    seat_id: f.seat_id.trim() || null,
    contact_email: f.contact_email.trim() || null,
    billing_event: f.billing_event,
    freq_cap_on_win: f.freq_cap_on_win,
    notes: f.notes.trim() || null,
  }
}

export default function SupplyPage() {
  const { data: partners } = useSWR<SupplyPartner[]>("/supply-partners", fetcher)
  // Публичный URL Go-биддера (audit R-01): SSP должны получать адрес bidder-go,
  // а не /api/rtb/bid/... backend'а — Python-биддер в production выключен.
  const { data: publicSettings } = useSWR<PublicSettings>("/settings/public", fetcher)
  const bidderBase = publicSettings?.bidder_base_url ?? null
  const bidUrl = (p: SupplyPartner) => (bidderBase ? `${bidderBase}/rtb/bid/${p.endpoint_key}` : null)

  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<SupplyPartner | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Токен показывается один раз после выдачи; результат проверки разворачивается
  const [issuedToken, setIssuedToken] = useState<{
    partnerId: string
    token: string
    prevValidUntil: string | null
  } | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [checkingId, setCheckingId] = useState<string | null>(null)
  const [statsId, setStatsId] = useState<string | null>(null)
  const [syncId, setSyncId] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [copied, setCopied] = useState<string | null>(null)

  const sorted = useMemo(
    () => (partners || []).slice().sort((a, b) => (a.created_at < b.created_at ? 1 : -1)),
    [partners],
  )

  const openCreate = () => {
    setEditing(null)
    setForm(EMPTY_FORM)
    setError(null)
    setOpen(true)
  }

  const openEdit = (p: SupplyPartner) => {
    setEditing(p)
    setForm(partnerToForm(p))
    setError(null)
    setOpen(true)
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    if (form.allowed.length === 0) {
      setError("Выберите хотя бы один формат инвентаря")
      return
    }
    setSaving(true)
    setError(null)
    try {
      if (editing) {
        await api(`/supply-partners/${editing.id}`, { method: "PATCH", body: formToPayload(form) })
      } else {
        const created = await api<SupplyPartner & { token?: string }>("/supply-partners", {
          method: "POST",
          body: formToPayload(form),
        })
        if (created?.token) setIssuedToken({ partnerId: created.id, token: created.token, prevValidUntil: null })
      }
      await mutate("/supply-partners")
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка сохранения")
    } finally {
      setSaving(false)
    }
  }

  // Ошибки действий над карточкой (422 «сначала выпустите токен» и т.п.) —
  // иначе клик молча ничего не делает.
  const runAction = async (fn: () => Promise<void>) => {
    setActionError(null)
    try {
      await fn()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Не удалось выполнить действие")
    }
  }

  const setStatus = (p: SupplyPartner, status: string) =>
    runAction(async () => {
      await api(`/supply-partners/${p.id}`, { method: "PATCH", body: { status } })
      await mutate("/supply-partners")
    })

  const runCheck = async (p: SupplyPartner) => {
    setCheckingId(p.id)
    setActionError(null)
    try {
      await api(`/supply-partners/${p.id}/check`, { method: "POST", body: {} })
      await mutate("/supply-partners")
      setExpandedId(p.id)
    } catch (e) {
      // Результаты шагов приходят в last_check с бэка; сюда попадает только
      // отказ до запуска (например 422 «сначала выпустите токен»).
      if (e instanceof ApiError && e.status === 422) setActionError(e.message)
    } finally {
      setCheckingId(null)
    }
  }

  const rotateToken = (p: SupplyPartner) =>
    runAction(async () => {
      if (
        p.has_auth_token &&
        !window.confirm(
          `Перевыпустить токен «${p.name}»? Старый токен будет приниматься ещё 15 минут — успейте передать новый партнёру.`,
        )
      )
        return
      const res = await api<{ token: string; prev_token_valid_until?: string | null }>(
        `/supply-partners/${p.id}/token`,
        { method: "POST" },
      )
      if (res?.token) {
        setIssuedToken({ partnerId: p.id, token: res.token, prevValidUntil: res.prev_token_valid_until ?? null })
      }
      await mutate("/supply-partners")
    })

  const revokeToken = (p: SupplyPartner) =>
    runAction(async () => {
      if (
        !window.confirm(
          `Отозвать токен «${p.name}»? Партнёр будет поставлен на паузу, биддер перестанет отвечать на его запросы.`,
        )
      )
        return
      await api(`/supply-partners/${p.id}/token`, { method: "DELETE" })
      if (issuedToken?.partnerId === p.id) setIssuedToken(null)
      await mutate("/supply-partners")
    })

  const archive = async (p: SupplyPartner) => {
    if (!window.confirm(`Архивировать «${p.name}»? Биддер перестанет принимать запросы по его ключу.`)) return
    await api(`/supply-partners/${p.id}`, { method: "PATCH", body: { status: "archived" } })
    await mutate("/supply-partners")
  }

  const copyEndpoint = (p: SupplyPartner) => {
    const url = bidUrl(p)
    if (!url) {
      setActionError(
        "PUBLIC_BIDDER_BASE_URL не настроен на сервере — публичный URL биддера неизвестен. Задайте переменную в .env и перезапустите backend.",
      )
      return
    }
    navigator.clipboard?.writeText(url)
    setCopied(p.id)
    setTimeout(() => setCopied(null), 1500)
  }

  const toggleAllowed = (v: string) =>
    setForm((f) => ({
      ...f,
      allowed: f.allowed.includes(v) ? f.allowed.filter((x) => x !== v) : [...f.allowed, v],
    }))

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Подключение SSP</h1>
          <p className="mt-0.5 text-sm text-muted">
            Онбординг supply-партнёров: анкета, эндпоинт, токен и автопроверка подключения
          </p>
        </div>
        <Button onClick={openCreate}>Подключить SSP</Button>
      </div>

      {/* Чек-лист требований для обмена с SSP */}
      <Card>
        <CardHeader
          title="Что запросить у SSP перед подключением"
          subtitle="Эти данные присылает SSP — SuperAdmin вносит их в анкету при создании партнёра"
        />
        <div className="grid grid-cols-1 gap-x-8 gap-y-1.5 px-6 pb-4 text-sm text-muted sm:grid-cols-2">
          <p>1. Версия OpenRTB (2.5 / 2.6), формат обмена JSON</p>
          <p>2. Тип аукциона: первая или вторая цена</p>
          <p>3. Валюта расчётов (модель CPM)</p>
          <p>4. Таймаут ответа tmax, мс</p>
          <p>5. Ожидаемый QPS (запросов в секунду)</p>
          <p>6. Форматы инвентаря: баннер / натив / видео / push</p>
          <p>7. IP-адреса серверов SSP для whitelist</p>
          <p>8. Seat ID, присвоенный нам как байеру</p>
          <p>9. Технический контакт для интеграции</p>
          <p>10. Bid floor на стороне SSP, если есть</p>
        </div>
        <div className="border-t border-border px-6 py-3 text-sm text-muted">
          Мы в ответ выдаём: <span className="font-medium text-foreground">bid-эндпоинт</span>{" "}
          <code className="rounded bg-panel px-1.5 py-0.5 font-mono text-xs">
            POST {bidderBase ?? "https://<bidder>"}/rtb/bid/{"{key}"}
          </code>{" "}
          {!publicSettings || bidderBase ? null : (
            <span className="text-danger">(PUBLIC_BIDDER_BASE_URL не настроен — URL для SSP неизвестен)</span>
          )}{" "}
          и{" "}
          <span className="font-medium text-foreground">секретный токен</span> (заголовок{" "}
          <code className="rounded bg-panel px-1.5 py-0.5 font-mono text-xs">X-CentralDSP-Token</code>). Макрос{" "}
          <code className="rounded bg-panel px-1.5 py-0.5 font-mono text-xs">{"${AUCTION_PRICE}"}</code> — в nurl (win
          notice) и burl (billable impression); расход списывается по событию из настроек партнёра.
        </div>
      </Card>

      {/* Выданный токен — показывается один раз */}
      {issuedToken ? (
        <div className="space-y-2 rounded-xl border border-warning/40 bg-warning/5 p-4">
          <p className="text-sm font-medium text-foreground">
            Секретный токен для «{sorted.find((p) => p.id === issuedToken.partnerId)?.name || "SSP"}» — передайте
            партнёру сейчас, он больше не будет показан:
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="break-all rounded bg-panel px-2 py-1 font-mono text-xs text-foreground">
              {issuedToken.token}
            </code>
            <Button variant="secondary" onClick={() => navigator.clipboard?.writeText(issuedToken.token)}>
              Копировать
            </Button>
            <Button variant="ghost" onClick={() => setIssuedToken(null)}>
              Скрыть
            </Button>
          </div>
          {issuedToken.prevValidUntil ? (
            <p className="text-xs text-muted">
              Предыдущий токен принимается до {new Date(issuedToken.prevValidUntil).toLocaleTimeString("ru-RU")} —
              после этого запросы со старым токеном получат 403.
            </p>
          ) : null}
        </div>
      ) : null}

      {actionError ? (
        <div role="alert" className="flex items-start justify-between gap-3 rounded-xl border border-danger/40 bg-danger/5 p-3 text-sm text-danger">
          <span>{actionError}</span>
          <button type="button" className="text-xs underline" onClick={() => setActionError(null)}>
            Скрыть
          </button>
        </div>
      ) : null}

      {/* Партнёры */}
      {!partners ? (
        <Spinner />
      ) : sorted.length === 0 ? (
        <Card>
          <EmptyState
            title="Нет подключённых SSP"
            description="Нажмите «Подключить SSP» и заполните анкету по данным партнёра."
            action={<Button onClick={openCreate}>Подключить SSP</Button>}
          />
        </Card>
      ) : (
        <div className="space-y-3">
          {sorted.map((p) => {
            const check = (p.last_check as SspCheckResult | null) || null
            const expanded = expandedId === p.id
            return (
              <Card key={p.id}>
                <div className="flex flex-wrap items-start justify-between gap-3 px-6 py-4">
                  <div className="min-w-0 space-y-1.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-foreground">{p.name}</span>
                      <StatusBadge status={p.status} />
                      {check ? (
                        <span
                          className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${
                            check.passed ? "bg-success/10 text-success" : "bg-danger/10 text-danger"
                          }`}
                        >
                          {check.passed ? "Проверка пройдена" : "Проверка не пройдена"}
                        </span>
                      ) : (
                        <span className="inline-flex items-center rounded-md bg-muted/10 px-2 py-0.5 text-xs font-medium text-muted">
                          Не проверялся
                        </span>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => copyEndpoint(p)}
                      className="rounded bg-panel px-2 py-1 font-mono text-xs text-accent hover:bg-border/50"
                      title="Скопировать URL эндпоинта"
                    >
                      {copied === p.id ? "Скопировано" : `POST …/rtb/bid/${p.endpoint_key}`}
                    </button>
                    <p className="text-xs text-muted">
                      OpenRTB {p.protocol_version || "2.5"} · {p.auction_type === 1 ? "1-я цена" : "2-я цена"} ·{" "}
                      {p.currency || "RUB"} · tmax {p.tmax_ms ?? 200} мс · до {p.qps_limit} QPS ·{" "}
                      {(p.allowed_ad_types || []).map((t) => AD_TYPE_LABELS[t] || t).join(", ")}
                      {p.ip_whitelist && p.ip_whitelist.length > 0 ? ` · IP-whitelist: ${p.ip_whitelist.length}` : ""}
                      {p.seat_id ? ` · seat ${p.seat_id}` : ""}
                      {` · расход по ${p.billing_event === "burl" ? "burl" : "nurl"}`}
                      {p.freq_cap_on_win ? " · cap по win" : ""}
                      {p.identity?.sync_enabled ? " · ID sync" : ""}
                      {p.has_auth_token ? ` · токен ${p.auth_token_hint || "задан"}` : " · токен не выдан"}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="inline-flex items-center gap-1">
                      <Button
                        variant="secondary"
                        onClick={() => runCheck(p)}
                        disabled={checkingId === p.id || !p.has_auth_token}
                      >
                        {checkingId === p.id ? "Проверяем…" : "Проверить подключение"}
                      </Button>
                      {!p.has_auth_token ? (
                        <HelpTip text="Сначала выдайте токен: без него биддер отвечает 403 на любой запрос партнёра." />
                      ) : null}
                    </span>
                    {p.status === "draft" || p.status === "paused" ? (
                      <Button variant="secondary" onClick={() => setStatus(p, "testing")} disabled={!p.has_auth_token}>
                        В тест
                      </Button>
                    ) : null}
                    {p.status === "testing" ? (
                      <span className="inline-flex items-center gap-1">
                        <Button onClick={() => setStatus(p, "active")} disabled={!check?.passed}>
                          Включить
                        </Button>
                        {!check?.passed ? (
                          <HelpTip text="Включение доступно после успешной автопроверки подключения." />
                        ) : null}
                      </span>
                    ) : null}
                    {p.status === "active" ? (
                      <Button variant="secondary" onClick={() => setStatus(p, "paused")}>
                        Пауза
                      </Button>
                    ) : null}
                    <Button
                      variant={statsId === p.id ? "secondary" : "ghost"}
                      onClick={() => setStatsId(statsId === p.id ? null : p.id)}
                      aria-expanded={statsId === p.id}
                    >
                      Статистика
                    </Button>
                    <Button
                      variant={syncId === p.id ? "secondary" : "ghost"}
                      onClick={() => setSyncId(syncId === p.id ? null : p.id)}
                      aria-expanded={syncId === p.id}
                    >
                      ID sync
                    </Button>
                    <Button variant="ghost" onClick={() => openEdit(p)}>
                      Изменить
                    </Button>
                    <Button variant="ghost" onClick={() => rotateToken(p)}>
                      {p.has_auth_token ? "Перевыпустить токен" : "Выдать токен"}
                    </Button>
                    {p.has_auth_token ? (
                      <Button variant="ghost" onClick={() => revokeToken(p)}>
                        Отозвать токен
                      </Button>
                    ) : null}
                    <Button variant="ghost" onClick={() => archive(p)}>
                      Архив
                    </Button>
                  </div>
                </div>

                {/* Результаты автопроверки */}
                {check ? (
                  <div className="border-t border-border">
                    <button
                      type="button"
                      className="flex w-full items-center justify-between px-6 py-2 text-left text-sm text-muted transition-colors hover:text-foreground"
                      onClick={() => setExpandedId(expanded ? null : p.id)}
                      aria-expanded={expanded}
                    >
                      <span>
                        Автопроверка от {new Date(check.ran_at).toLocaleString("ru-RU")} —{" "}
                        {check.checks.filter((c) => c.ok).length}/{check.checks.length} шагов пройдено
                      </span>
                      <span aria-hidden className="text-xs">
                        {expanded ? "▲" : "▼"}
                      </span>
                    </button>
                    {expanded ? (
                      <ul className="space-y-1.5 px-6 pb-4">
                        {check.checks.map((c) => (
                          <li key={c.key} className="flex flex-wrap items-start gap-2 text-sm">
                            <span
                              aria-hidden
                              className={`mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${
                                c.ok ? "bg-success/15 text-success" : "bg-danger/15 text-danger"
                              }`}
                            >
                              {c.ok ? "✓" : "✕"}
                            </span>
                            <span className="font-medium text-foreground">{c.label}</span>
                            <span className="text-muted">{c.detail}</span>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                ) : null}

                {statsId === p.id ? <SspPartnerStats partner={p} /> : null}
                {syncId === p.id ? <SspIdSync partner={p} /> : null}
              </Card>
            )
          })}
        </div>
      )}

      {/* Анкета подключения */}
      <Modal open={open} onClose={() => setOpen(false)} title={editing ? `Настройки «${editing.name}»` : "Подключить SSP"} wide>
        <form onSubmit={save} className="space-y-4">
          <Field label="Название SSP">
            <input
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className={inputCls}
              placeholder="AdMixer / Buzzoola / MyTarget SSP"
            />
          </Field>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Версия OpenRTB" hint="Версия протокола, по которой SSP шлёт BidRequest">
              <select
                value={form.protocol_version}
                onChange={(e) => setForm({ ...form, protocol_version: e.target.value })}
                className={inputCls}
              >
                <option value="2.5">OpenRTB 2.5</option>
                <option value="2.6">OpenRTB 2.6</option>
              </select>
            </Field>
            <Field label="Тип аукциона">
              <select
                value={form.auction_type}
                onChange={(e) => setForm({ ...form, auction_type: e.target.value })}
                className={inputCls}
              >
                <option value="1">Первая цена</option>
                <option value="2">Вторая цена</option>
              </select>
            </Field>
            <Field label="Валюта расчётов">
              <select
                value={form.currency}
                onChange={(e) => setForm({ ...form, currency: e.target.value })}
                className={inputCls}
              >
                <option value="RUB">RUB</option>
                <option value="USD">USD</option>
                <option value="EUR">EUR</option>
              </select>
            </Field>
            <Field label="Таймаут tmax, мс" hint="За сколько миллисекунд SSP ждёт наш ответ (50–1000)">
              <input
                type="number"
                min="50"
                max="1000"
                value={form.tmax_ms}
                onChange={(e) => setForm({ ...form, tmax_ms: e.target.value })}
                className={inputCls}
              />
            </Field>
            <Field label="Лимит QPS" hint="Максимум bid-запросов в секунду от этого SSP">
              <input
                type="number"
                min="1"
                max="100000"
                value={form.qps_limit}
                onChange={(e) => setForm({ ...form, qps_limit: e.target.value })}
                className={inputCls}
              />
            </Field>
            <Field label="Bid floor, CPM" hint="Минимальная ставка на нашей стороне. 0 — без пола.">
              <input
                type="number"
                min="0"
                step="0.01"
                value={form.bid_floor}
                onChange={(e) => setForm({ ...form, bid_floor: e.target.value })}
                className={inputCls}
              />
            </Field>
          </div>

          <Field label="Форматы инвентаря" hint="Какие типы креативов этот SSP принимает — биддер отвечает только на подходящие запросы">
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(AD_TYPE_LABELS).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => toggleAllowed(value)}
                  className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
                    form.allowed.includes(value)
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-border text-muted hover:text-foreground"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </Field>

          <Field
            label="IP-адреса серверов SSP (whitelist)"
            hint="По одному IP или CIDR на строку. Пусто — принимать запросы с любых IP."
          >
            <textarea
              rows={3}
              value={form.ip_whitelist}
              onChange={(e) => setForm({ ...form, ip_whitelist: e.target.value })}
              className={inputCls + " resize-y font-mono text-xs"}
              placeholder={"77.88.0.0/18\n5.255.192.0/18"}
            />
          </Field>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Seat ID" hint="ID, который SSP присвоил нам как байеру. Подставляется в seatbid.seat.">
              <input
                value={form.seat_id}
                onChange={(e) => setForm({ ...form, seat_id: e.target.value })}
                className={inputCls}
                placeholder="seat-12345"
              />
            </Field>
            <Field label="Технический контакт">
              <input
                type="email"
                value={form.contact_email}
                onChange={(e) => setForm({ ...form, contact_email: e.target.value })}
                className={inputCls}
                placeholder="rtb@ssp.example"
              />
            </Field>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field
              label="Событие расхода"
              hint="burl — деньги списываются по billable impression (OpenRTB), nurl — только выигрыш и бронь. win — списание на nurl, как до сверки с отчётами SSP."
            >
              <select
                value={form.billing_event}
                onChange={(e) => setForm({ ...form, billing_event: e.target.value === "win" ? "win" : "burl" })}
                className={inputCls}
              >
                <option value="burl">burl — billable impression (рекомендуется)</option>
                <option value="win">win — списание на nurl</option>
              </select>
            </Field>
            <Field
              label="Частотный лимит"
              hint="По умолчанию частота учитывается по пикселю показа. Включите, если SSP не отдаёт imp-пиксель — тогда лимит считается по выигрышу."
            >
              <label className="flex items-center gap-2 py-2 text-sm text-foreground">
                <input
                  type="checkbox"
                  checked={form.freq_cap_on_win}
                  onChange={(e) => setForm({ ...form, freq_cap_on_win: e.target.checked })}
                  className="h-4 w-4 rounded border-border accent-accent"
                />
                Считать частоту по выигрышу (nurl)
              </label>
            </Field>
          </div>

          <Field label="Заметки">
            <input
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              className={inputCls}
              placeholder="Условия, договорённости, статус интеграции"
            />
          </Field>

          {error ? <p className="text-xs text-danger">{error}</p> : null}

          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? "Сохраняем…" : editing ? "Сохранить" : "Создать и выдать доступы"}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  )
}
