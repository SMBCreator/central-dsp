"use client"

import { useState } from "react"
import useSWR from "swr"
import { api, fetcher } from "@/lib/api"
import { Button, Card, CardHeader, EmptyState, Field, PillTabs, Spinner, Table } from "@/components/ui"

type AppSettings = {
  currency: string
  fx_markup_pct: number
  default_margin_pct: number
}

type FxInfo = {
  currency: string
  cbr_rate: number
  markup_percent: number
  effective_rate: number
}

type DomainItem = {
  id: string
  domain: string
  list_type: "black" | "white"
  source: string | null
  created_at: string
}

const LIST_TABS = [
  { key: "black", label: "Чёрный список" },
  { key: "white", label: "Белый список" },
] as const

const inputCls =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition-colors focus:border-ring focus:ring-2 focus:ring-ring/30"


type OrdSettings = {
  configured: boolean
  provider: string
  environment?: string
  api_key_masked?: string
  note?: string
}

function OrdCard() {
  const { data: ord, mutate } = useSWR<OrdSettings>("/settings/ord", fetcher)
  const [apiKey, setApiKey] = useState("")
  const [env, setEnv] = useState<"production" | "sandbox">("production")
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null)

  async function save() {
    if (apiKey.trim().length < 8) return
    setSaving(true)
    setMsg(null)
    try {
      await api("/settings/ord", {
        method: "PUT",
        body: JSON.stringify({ api_key: apiKey.trim(), environment: env }),
      })
      setApiKey("")
      await mutate()
      setMsg({ kind: "ok", text: "Ключ сохранён. Нажмите «Проверить связь», чтобы убедиться, что ОРД отвечает." })
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : "Не удалось сохранить ключ" })
    } finally {
      setSaving(false)
    }
  }

  async function test() {
    setTesting(true)
    setMsg(null)
    try {
      const r = await api<{ ok: boolean; provider: string; note?: string }>("/settings/ord/test", { method: "POST" })
      setMsg({
        kind: r.provider === "ozon" ? "ok" : "err",
        text: r.provider === "ozon" ? "Связь с ОРД Озон установлена — ключ рабочий." : (r.note ?? "Работает mock-режим"),
      })
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : "ОРД не отвечает — проверьте ключ и окружение" })
    } finally {
      setTesting(false)
    }
  }

  async function disconnect() {
    await api("/settings/ord", { method: "DELETE" })
    setMsg(null)
    await mutate()
  }

  return (
    <Card>
      <CardHeader
        title="ОРД Озон (маркировка рекламы)"
        subtitle="API-ключ из кабинета ОРД Озон → Настройки → API. С ключом креативы регистрируются по-настоящему и erid виден в вашем кабинете ОРД; без ключа работает mock-режим с фиктивными erid."
      />
      <div className="space-y-4 p-4 pt-0">
        {ord ? (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span
              className={
                "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium " +
                (ord.configured ? "bg-success/10 text-success" : "bg-warning/10 text-warning")
              }
            >
              {ord.configured ? `Подключено: Ozon (${ord.environment === "sandbox" ? "песочница" : "прод"})` : "Mock-режим"}
            </span>
            {ord.configured && <span className="font-mono text-xs text-muted">{ord.api_key_masked}</span>}
            {!ord.configured && ord.note && <span className="text-xs text-muted">{ord.note}</span>}
          </div>
        ) : (
          <Spinner />
        )}

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div className="sm:col-span-2">
            <Field label="API-ключ" hint="Хранится в БД, наружу отдаётся только маска">
              <input
                className={inputCls}
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={ord?.configured ? "Ввести новый ключ (заменит текущий)" : "Вставьте ключ ОРД Озон"}
                autoComplete="off"
              />
            </Field>
          </div>
          <Field label="Окружение" hint="Песочница — для проверки интеграции без публикации">
            <select className={inputCls} value={env} onChange={(e) => setEnv(e.target.value as "production" | "sandbox")}>
              <option value="production">Прод (api-ord.ozon.ru)</option>
              <option value="sandbox">Песочница (ord-sandbox.ozon.ru)</option>
            </select>
          </Field>
        </div>

        {msg && (
          <p className={"text-sm " + (msg.kind === "ok" ? "text-success" : "text-danger")} role="status">
            {msg.text}
          </p>
        )}

        <div className="flex flex-wrap gap-2">
          <Button onClick={save} disabled={saving || apiKey.trim().length < 8}>
            {saving ? "Сохранение…" : "Сохранить ключ"}
          </Button>
          <Button variant="secondary" onClick={test} disabled={testing}>
            {testing ? "Проверка…" : "Проверить связь"}
          </Button>
          {ord?.configured && (
            <Button variant="secondary" onClick={disconnect}>
              Отключить (вернуть mock)
            </Button>
          )}
        </div>
      </div>
    </Card>
  )
}

export default function SettingsPage() {
  const { data: settings, mutate: mutateSettings } = useSWR<AppSettings>("/settings", fetcher)
  const { data: fx } = useSWR<FxInfo>("/settings/fx?currency=USD", fetcher)
  const [listType, setListType] = useState<(typeof LIST_TABS)[number]["key"]>("black")
  const { data: domains, mutate: mutateDomains } = useSWR<DomainItem[]>(
    `/settings/domain-lists?list_type=${listType}`,
    fetcher,
  )

  const [markup, setMarkup] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [newDomain, setNewDomain] = useState("")
  const [adding, setAdding] = useState(false)

  async function saveMarkup() {
    if (markup === null) return
    setSaving(true)
    try {
      await api("/settings", { method: "PATCH", body: JSON.stringify({ fx_markup_pct: Number(markup) }) })
      setMarkup(null)
      await mutateSettings()
    } finally {
      setSaving(false)
    }
  }

  async function addDomain() {
    const d = newDomain.trim().toLowerCase()
    if (!d) return
    setAdding(true)
    try {
      await api("/settings/domain-lists", {
        method: "POST",
        body: JSON.stringify({ domain: d, list_type: listType, source: "manual" }),
      })
      setNewDomain("")
      await mutateDomains()
    } finally {
      setAdding(false)
    }
  }

  async function removeDomain(id: string) {
    await api(`/settings/domain-lists/${id}`, { method: "DELETE" })
    await mutateDomains()
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">Настройки</h1>
        <p className="mt-0.5 text-sm text-muted">Валюта, курс, глобальные списки площадок</p>
      </div>

      <Card>
        <CardHeader title="Валюта и курс" subtitle="Кабинет ведётся в рублях; закупка в $ конвертируется по курсу ЦБ РФ с наценкой" />
        {settings && fx ? (
          <div className="grid grid-cols-1 gap-4 p-4 pt-0 sm:grid-cols-3">
            <Field label="Валюта кабинета">
              <input className={inputCls} value={settings.currency} disabled />
            </Field>
            <Field label="Курс ЦБ USD/RUB" hint={`Эффективный с наценкой ${fx.markup_percent}%: ${Number(fx.effective_rate).toFixed(2)} ₽`}>
              <input className={inputCls} value={Number(fx.cbr_rate).toFixed(2)} disabled />
            </Field>
            <Field label="Наценка на курс, %" hint="Страховка от волатильности (стандарт 2–5%)">
              <div className="flex gap-2">
                <input
                  className={inputCls}
                  type="number"
                  step="0.5"
                  min="0"
                  max="50"
                  value={markup ?? String(settings.fx_markup_pct)}
                  onChange={(e) => setMarkup(e.target.value)}
                />
                <Button onClick={saveMarkup} disabled={saving || markup === null}>
                  {saving ? "..." : "Сохранить"}
                </Button>
              </div>
            </Field>
          </div>
        ) : (
          <Spinner />
        )}
      </Card>

      <OrdCard />

      <Card>
        <CardHeader
          title="Глобальные списки площадок"
          subtitle="Применяются ко всем кампаниям на этапе бида. Чёрный список — домен никогда не выкупается; белый — если он не пуст, выкупаются только домены из него."
          action={<PillTabs value={listType} onChange={setListType} options={LIST_TABS} />}
        />
        <div className="flex gap-2 px-4 pb-3">
          <input
            className={inputCls}
            value={newDomain}
            onChange={(e) => setNewDomain(e.target.value)}
            placeholder="example.com или com.app.bundle"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) addDomain()
            }}
          />
          <Button onClick={addDomain} disabled={adding || !newDomain.trim()}>
            {adding ? "..." : "Добавить"}
          </Button>
        </div>
        {domains ? (
          domains.length > 0 ? (
            <Table headers={["Домен", "Источник", "Добавлен", ""]}>
              {domains.map((d) => (
                <tr key={d.id} className="hover:bg-surface">
                  <td className="px-4 py-3 font-mono text-xs font-medium text-foreground">{d.domain}</td>
                  <td className="px-4 py-3 text-xs text-muted">
                    {d.source === "stats_click" ? "из статистики" : "вручную"}
                  </td>
                  <td className="px-4 py-3 text-xs text-muted">{String(d.created_at).slice(0, 10)}</td>
                  <td className="px-4 py-3 text-right">
                    <Button variant="secondary" onClick={() => removeDomain(d.id)}>
                      Убрать
                    </Button>
                  </td>
                </tr>
              ))}
            </Table>
          ) : (
            <EmptyState
              title={listType === "black" ? "Чёрный список пуст" : "Белый список пуст"}
              description={
                listType === "black"
                  ? "Добавьте домен вручную или одним кликом из Аналитики → Площадки."
                  : "Пока белый список пуст, закупка идёт на всех площадках, кроме чёрного списка."
              }
            />
          )
        ) : (
          <Spinner />
        )}
      </Card>
    </div>
  )
}
