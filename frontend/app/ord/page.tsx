"use client"

import { useState } from "react"
import useSWR, { mutate } from "swr"
import { api, fetcher } from "@/lib/api"
import { isAdminRole } from "@/lib/roles"
import { useMe } from "@/lib/use-me"
import { Button, Card, CardHeader, EmptyState, Field, inputCls, Modal, PillTabs, Spinner, StatusBadge, Table } from "@/components/ui"
import { OrdInvoicesTab } from "@/components/ord-invoices"

interface Counterparty {
  id: string
  name: string
  inn: string
  ogrn?: string | null
  legal_type: string
  role: string
  ord_status: string
  ord_error?: string | null
  /** наша организация (ОРС): isOpc в ОРД, владелец сводной площадки */
  is_self?: boolean
  rs_url?: string | null
}

interface OrdContract {
  id: string
  number: string
  signed_at: string
  contract_type: string
  client_id: string
  contractor_id: string
  client_name: string
  contractor_name: string
  ord_status: string
  ord_error?: string | null
  is_vat?: boolean | null
}

interface OrdCreative {
  id: string
  creative_id: string
  creative_name: string
  erid?: string | null
  status: string
  error?: string | null
  registered_at?: string | null
}

/** Версия маркировки креатива (ord_creatives): erid действует с registered_at до superseded_at. */
interface EridVersion {
  id: string
  version: number
  erid?: string | null
  status: string
  contract_id?: string | null
  registered_at?: string | null
  superseded_at?: string | null
  error?: string | null
  is_current: boolean
}

interface OrdExport {
  id: string
  period: string
  status: string
  rows_exported?: number | null
  error?: string | null
  started_at: string
}

interface CreativeLite {
  id: string
  name: string
  erid?: string | null
}

const LEGAL_LABELS: Record<string, string> = { ul: "Юрлицо", ip: "ИП", fl: "Физлицо", foreign: "Иностр." }
const ROLE_LABELS: Record<string, string> = { advertiser: "Рекламодатель", agency: "Агентство", publisher: "Издатель", ors: "ОРС" }

type Tab = "counterparties" | "contracts" | "creatives" | "invoices" | "exports"

const TABS = [
  { key: "counterparties", label: "Контрагенты" },
  { key: "contracts", label: "Договоры" },
  { key: "creatives", label: "Креативы / erid" },
  { key: "invoices", label: "Акты" },
  { key: "exports", label: "Выгрузки" },
] as const

export default function OrdPage() {
  const { me } = useMe()
  const [tab, setTab] = useState<Tab>("counterparties")
  const { data: counterparties } = useSWR<Counterparty[]>("/ord/counterparties", fetcher)
  const { data: contracts } = useSWR<OrdContract[]>("/ord/contracts", fetcher)
  const { data: ordCreatives } = useSWR<OrdCreative[]>("/ord/creatives", fetcher)
  const { data: exports } = useSWR<OrdExport[]>("/ord/exports", fetcher)
  const { data: allCreatives } = useSWR<CreativeLite[]>("/creatives", fetcher)

  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  // --- модалка контрагента
  const [cpOpen, setCpOpen] = useState(false)
  const [cpSaving, setCpSaving] = useState(false)
  const [cpForm, setCpForm] = useState({ name: "", inn: "", ogrn: "", legal_type: "ul", role: "advertiser", address: "", is_self: false, rs_url: "" })

  // --- модалка договора
  const [ctOpen, setCtOpen] = useState(false)
  const [ctSaving, setCtSaving] = useState(false)
  const [ctForm, setCtForm] = useState({ client_id: "", contractor_id: "", number: "", signed_at: "", contract_type: "service" })

  // --- модалка регистрации креатива
  const [crOpen, setCrOpen] = useState(false)
  const [crSaving, setCrSaving] = useState(false)
  const [crForm, setCrForm] = useState({ creative_id: "", contract_id: "" })

  // --- история версий erid креатива
  const [versionsOf, setVersionsOf] = useState<OrdCreative | null>(null)
  const { data: versions } = useSWR<EridVersion[]>(versionsOf ? `/ord/creatives/${versionsOf.creative_id}/versions` : null, fetcher)

  const registeredContracts = (contracts || []).filter((c) => c.ord_status === "registered")
  const unregisteredCreatives = (allCreatives || []).filter((c) => !c.erid)

  const saveCp = async (e: React.FormEvent) => {
    e.preventDefault()
    setCpSaving(true)
    setError(null)
    try {
      await api("/ord/counterparties", {
        method: "POST",
        body: { ...cpForm, rs_url: cpForm.rs_url.trim() || null, role: cpForm.is_self ? "ors" : cpForm.role },
      })
      await mutate("/ord/counterparties")
      setCpOpen(false)
      setCpForm({ name: "", inn: "", ogrn: "", legal_type: "ul", role: "advertiser", address: "", is_self: false, rs_url: "" })
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка сохранения")
    } finally {
      setCpSaving(false)
    }
  }

  const saveCt = async (e: React.FormEvent) => {
    e.preventDefault()
    setCtSaving(true)
    setError(null)
    try {
      await api("/ord/contracts", { method: "POST", body: ctForm })
      await mutate("/ord/contracts")
      setCtOpen(false)
      setCtForm({ client_id: "", contractor_id: "", number: "", signed_at: "", contract_type: "service" })
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка сохранения")
    } finally {
      setCtSaving(false)
    }
  }

  const registerCreative = async (e: React.FormEvent) => {
    e.preventDefault()
    setCrSaving(true)
    setError(null)
    try {
      await api(`/ord/creatives/${crForm.creative_id}/register`, {
        method: "POST",
        body: { contract_id: crForm.contract_id || null },
      })
      await Promise.all([mutate("/ord/creatives"), mutate("/creatives")])
      setCrOpen(false)
      setCrForm({ creative_id: "", contract_id: "" })
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка регистрации")
    } finally {
      setCrSaving(false)
    }
  }

  const registerEntity = async (kind: "counterparties" | "contracts", id: string) => {
    setBusyId(id)
    setError(null)
    try {
      await api(`/ord/${kind}/${id}/register`, { method: "POST" })
      await mutate(`/ord/${kind}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка регистрации в ОРД")
    } finally {
      setBusyId(null)
    }
  }

  return (
    <main className="space-y-4 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-foreground">Маркировка (ОРД)</h1>
          <p className="text-xs text-muted">
            Контрагенты, договоры и erid-токены — регистрация в ОРД Ozon. erid присваивается автоматически при одобрении
            креатива модератором; здесь — ручная регистрация для креативов, у которых договор появился позже.
          </p>
        </div>
        <PillTabs value={tab} onChange={setTab} options={TABS} />
      </div>

      {error ? (
        <div role="alert" className="rounded-2xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      ) : null}

      <ol className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted" aria-label="Порядок регистрации в ОРД">
        <li className="flex items-center gap-1.5">
          <span className="font-mono text-foreground">1</span> Контрагенты (ИНН/ОГРН) → «В ОРД»
        </li>
        <li aria-hidden className="text-border">/</li>
        <li className="flex items-center gap-1.5">
          <span className="font-mono text-foreground">2</span> Договор между зарегистрированными сторонами → «В ОРД»
        </li>
        <li aria-hidden className="text-border">/</li>
        <li className="flex items-center gap-1.5">
          <span className="font-mono text-foreground">3</span> Креатив с ККТУ одобрен модератором → erid автоматически
        </li>
        <li aria-hidden className="text-border">/</li>
        <li className="flex items-center gap-1.5">
          <span className="font-mono text-foreground">4</span> Статистика уходит автоматически 2-го числа за прошлый месяц
        </li>
        <li aria-hidden className="text-border">/</li>
        <li className="flex items-center gap-1.5">
          <span className="font-mono text-foreground">5</span> Акт за месяц по исполнительскому договору → «Отправить в ОРД»
        </li>
      </ol>

      {tab === "counterparties" ? (
        <Card>
          <CardHeader
            title="Контрагенты"
            subtitle="Юрлица и ИП, участвующие в рекламных договорах"
            action={<Button onClick={() => { setError(null); setCpOpen(true) }}>Добавить</Button>}
          />
          {!counterparties ? (
            <Spinner />
          ) : counterparties.length === 0 ? (
            <EmptyState title="Нет контрагентов" description="Добавьте контрагента, чтобы зарегистрировать договор в ОРД" />
          ) : (
            <Table headers={["Название", "ИНН", "Тип", "Роль", "Статус ОРД", ""]}>
              {counterparties.map((cp) => (
                <tr key={cp.id}>
                  <td className="px-4 py-3 font-medium text-foreground">{cp.name}</td>
                  <td className="px-4 py-3 font-mono text-xs text-muted">{cp.inn}</td>
                  <td className="px-4 py-3 text-muted">{LEGAL_LABELS[cp.legal_type] || cp.legal_type}</td>
                  <td className="px-4 py-3 text-muted">
                    {ROLE_LABELS[cp.role] || cp.role}
                    {cp.is_self ? (
                      <span className="ml-2 inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                        наша
                      </span>
                    ) : null}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={cp.ord_status} />
                    {cp.ord_status === "failed" && cp.ord_error ? (
                      <p className="mt-1 max-w-xs text-xs text-danger">{cp.ord_error}</p>
                    ) : null}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {cp.ord_status !== "registered" ? (
                      <Button
                        variant="secondary"
                        disabled={busyId === cp.id}
                        onClick={() => registerEntity("counterparties", cp.id)}
                      >
                        {busyId === cp.id ? "Регистрация…" : "В ОРД"}
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      ) : null}

      {tab === "contracts" ? (
        <Card>
          <CardHeader
            title="Договоры"
            subtitle="Изначальные и доходные договоры между контрагентами"
            action={<Button onClick={() => { setError(null); setCtOpen(true) }}>Добавить</Button>}
          />
          {!contracts ? (
            <Spinner />
          ) : contracts.length === 0 ? (
            <EmptyState title="Нет договоров" description="Договор нужен для получения erid на креативы" />
          ) : (
            <Table headers={["Номер", "Дата", "Заказчик", "Исполнитель", "Статус ОРД", ""]}>
              {contracts.map((ct) => (
                <tr key={ct.id}>
                  <td className="px-4 py-3 font-medium text-foreground">{ct.number}</td>
                  <td className="px-4 py-3 text-muted">{String(ct.signed_at).slice(0, 10)}</td>
                  <td className="px-4 py-3 text-muted">{ct.client_name}</td>
                  <td className="px-4 py-3 text-muted">{ct.contractor_name}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={ct.ord_status} />
                    {ct.ord_status === "failed" && ct.ord_error ? (
                      <p className="mt-1 max-w-xs text-xs text-danger">{ct.ord_error}</p>
                    ) : null}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {ct.ord_status !== "registered" ? (
                      <Button
                        variant="secondary"
                        disabled={busyId === ct.id}
                        onClick={() => registerEntity("contracts", ct.id)}
                      >
                        {busyId === ct.id ? "Регистрация…" : "В ОРД"}
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      ) : null}

      {tab === "creatives" ? (
        <Card>
          <CardHeader
            title="Креативы и erid"
            subtitle="Каждый креатив должен получить erid до запуска кампании"
            action={
              <Button
                onClick={() => { setError(null); setCrOpen(true) }}
                disabled={registeredContracts.length === 0 || unregisteredCreatives.length === 0}
              >
                Зарегистрировать креатив
              </Button>
            }
          />
          {!ordCreatives ? (
            <Spinner />
          ) : ordCreatives.length === 0 ? (
            <EmptyState
              title="Нет зарегистрированных креативов"
              description={
                registeredContracts.length === 0
                  ? "Сначала зарегистрируйте договор в ОРД"
                  : "Зарегистрируйте креатив, чтобы получить erid"
              }
            />
          ) : (
            <Table headers={["Креатив", "erid", "Статус", "Зарегистрирован", ""]}>
              {ordCreatives.map((oc) => (
                <tr key={oc.id}>
                  <td className="px-4 py-3 font-medium text-foreground">{oc.creative_name}</td>
                  <td className="px-4 py-3">
                    {oc.erid ? (
                      <code className="rounded-lg bg-panel px-2 py-1 font-mono text-xs text-foreground">{oc.erid}</code>
                    ) : (
                      <span className="text-xs text-muted">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={oc.status} />
                    {oc.status === "failed" && oc.error ? (
                      <p className="mt-1 max-w-xs text-xs text-danger">{oc.error}</p>
                    ) : null}
                  </td>
                  <td className="px-4 py-3 text-muted">
                    {oc.registered_at ? String(oc.registered_at).slice(0, 10) : "—"}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Button variant="secondary" onClick={() => setVersionsOf(oc)}>Версии</Button>
                  </td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      ) : null}

      {tab === "invoices" ? (
        <OrdInvoicesTab contracts={contracts} counterparties={counterparties} canWrite={isAdminRole(me?.role)} />
      ) : null}

      {tab === "exports" ? (
        <Card>
          <CardHeader
            title="Выгрузки в ОРД"
            subtitle="Показы и клики по erid за прошлый месяц — cron 2-го числа в 06:00 МСК. Статус «failed» — повторить можно вызовом /cron/ord-export?period=YYYY-MM (см. DEPLOYMENT.md), повтор за уже выгруженный период безопасен."
          />
          {!exports ? (
            <Spinner />
          ) : exports.length === 0 ? (
            <EmptyState title="Выгрузок ещё не было" description="Первая выгрузка запустится автоматически 2-го числа следующего месяца. В отчёт попадают только креативы с erid." />
          ) : (
            <Table headers={["Период", "Статус", "Строк", "Запущена", "Ошибка"]}>
              {exports.map((ex) => (
                <tr key={ex.id}>
                  <td className="px-4 py-3 font-medium text-foreground">{ex.period}</td>
                  <td className="px-4 py-3"><StatusBadge status={ex.status} /></td>
                  <td className="px-4 py-3 text-muted">{ex.rows_exported ?? "—"}</td>
                  <td className="px-4 py-3 text-muted">{String(ex.started_at).slice(0, 16).replace("T", " ")}</td>
                  <td className="px-4 py-3 text-xs text-danger">{ex.error || ""}</td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      ) : null}

      <Modal
        open={!!versionsOf}
        onClose={() => setVersionsOf(null)}
        title={`Версии erid — ${versionsOf?.creative_name ?? ""}`}
        subtitle="Новая версия появляется при смене контрагента или договора после выдачи erid; статистика привязывается к версии, действовавшей в час показа. Юридические поля креатива с erid заморожены (409 ord_locked) — для другого клиента создайте новый креатив."
        size="lg"
      >
        {!versions ? (
          <Spinner />
        ) : versions.length === 0 ? (
          <EmptyState title="Версий нет" description="Креатив ещё не регистрировался в ОРД" />
        ) : (
          <Table headers={["Версия", "erid", "Статус", "Действует с", "по", ""]}>
            {versions.map((v) => (
              <tr key={v.id}>
                <td className="px-4 py-2 font-mono text-xs text-foreground">v{v.version}</td>
                <td className="px-4 py-2 font-mono text-xs text-foreground">{v.erid || "—"}</td>
                <td className="px-4 py-2">
                  <StatusBadge status={v.status} />
                  {v.error ? <p className="mt-1 max-w-xs text-xs text-danger">{v.error}</p> : null}
                </td>
                <td className="px-4 py-2 text-muted">{v.registered_at ? String(v.registered_at).slice(0, 16).replace("T", " ") : "—"}</td>
                <td className="px-4 py-2 text-muted">{v.superseded_at ? String(v.superseded_at).slice(0, 16).replace("T", " ") : "—"}</td>
                <td className="px-4 py-2 text-xs text-primary">{v.is_current ? "текущая" : ""}</td>
              </tr>
            ))}
          </Table>
        )}
      </Modal>

      <Modal open={cpOpen} onClose={() => setCpOpen(false)} title="Новый контрагент">
        <form onSubmit={saveCp} className="space-y-4">
          <Field label="Название">
            <input required className={inputCls} value={cpForm.name} onChange={(e) => setCpForm({ ...cpForm, name: e.target.value })} placeholder="ООО Ромашка" />
          </Field>
          <Field label="ИНН" hint="10 цифр для юрлица, 12 для ИП/физлица">
            <input required minLength={5} className={inputCls} value={cpForm.inn} onChange={(e) => setCpForm({ ...cpForm, inn: e.target.value })} placeholder="7700000000" />
          </Field>
          <Field label="ОГРН / ОГРНИП">
            <input className={inputCls} value={cpForm.ogrn} onChange={(e) => setCpForm({ ...cpForm, ogrn: e.target.value })} placeholder="1027700000000" />
          </Field>
          <Field label="Местонахождение (юр. адрес)" hint="Требуется для регистрации в ОРД">
            <input className={inputCls} value={cpForm.address} onChange={(e) => setCpForm({ ...cpForm, address: e.target.value })} placeholder="г. Москва, ул. Ленина, д. 1" />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Тип">
              <select className={inputCls} value={cpForm.legal_type} onChange={(e) => setCpForm({ ...cpForm, legal_type: e.target.value })}>
                <option value="ul">Юрлицо</option>
                <option value="ip">ИП</option>
                <option value="fl">Физлицо</option>
                <option value="foreign">Иностранное</option>
              </select>
            </Field>
            <Field label="Роль">
              <select className={inputCls} value={cpForm.role} onChange={(e) => setCpForm({ ...cpForm, role: e.target.value })}>
                <option value="advertiser">Рекламодатель</option>
                <option value="agency">Агентство</option>
                <option value="publisher">Издатель</option>
                <option value="ors">ОРС</option>
              </select>
            </Field>
          </div>
          {isAdminRole(me?.role) && me?.role !== "manager" ? (
            <div className="space-y-3 rounded-xl border border-border bg-surface px-3 py-3">
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={cpForm.is_self}
                  onChange={(e) => setCpForm({ ...cpForm, is_self: e.target.checked, role: e.target.checked ? "ors" : cpForm.role })}
                />
                <span>
                  <span className="font-medium text-foreground">Наша организация (ОРС)</span>
                  <span className="block text-xs text-muted">
                    Оператор рекламной системы Central DSP. В ОРД уходит как ОРС, на неё регистрируется сводная площадка. Ровно одна — флаг переедет с прежней.
                  </span>
                </span>
              </label>
              {cpForm.is_self ? (
                <Field label="URL рекламной системы" hint="Домен Central DSP — он же URL сводной площадки в ОРД">
                  <input
                    className={inputCls}
                    type="url"
                    value={cpForm.rs_url}
                    onChange={(e) => setCpForm({ ...cpForm, rs_url: e.target.value })}
                    placeholder="https://central-dsp.ru"
                  />
                </Field>
              ) : null}
            </div>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setCpOpen(false)}>Отмена</Button>
            <Button type="submit" disabled={cpSaving}>{cpSaving ? "Сохранение…" : "Сохранить"}</Button>
          </div>
        </form>
      </Modal>

      <Modal open={ctOpen} onClose={() => setCtOpen(false)} title="Новый договор">
        <form onSubmit={saveCt} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Заказчик">
              <select required className={inputCls} value={ctForm.client_id} onChange={(e) => setCtForm({ ...ctForm, client_id: e.target.value })}>
                <option value="">— выбрать —</option>
                {(counterparties || []).map((cp) => (
                  <option key={cp.id} value={cp.id}>{cp.name}</option>
                ))}
              </select>
            </Field>
            <Field label="Исполнитель">
              <select required className={inputCls} value={ctForm.contractor_id} onChange={(e) => setCtForm({ ...ctForm, contractor_id: e.target.value })}>
                <option value="">— выбрать —</option>
                {(counterparties || []).map((cp) => (
                  <option key={cp.id} value={cp.id}>{cp.name}</option>
                ))}
              </select>
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Номер договора">
              <input required className={inputCls} value={ctForm.number} onChange={(e) => setCtForm({ ...ctForm, number: e.target.value })} placeholder="Д-2026/01" />
            </Field>
            <Field label="Дата подписания">
              <input required type="date" className={inputCls} value={ctForm.signed_at} onChange={(e) => setCtForm({ ...ctForm, signed_at: e.target.value })} />
            </Field>
          </div>
          <Field label="Тип договора">
            <select className={inputCls} value={ctForm.contract_type} onChange={(e) => setCtForm({ ...ctForm, contract_type: e.target.value })}>
              <option value="service">Оказание услуг</option>
              <option value="mediation">Посреднический</option>
              <option value="additional">Доп. соглашение</option>
            </select>
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setCtOpen(false)}>Отмена</Button>
            <Button type="submit" disabled={ctSaving}>{ctSaving ? "Сохранение…" : "Сохранить"}</Button>
          </div>
        </form>
      </Modal>

      <Modal open={crOpen} onClose={() => setCrOpen(false)} title="Регистрация креатива в ОРД">
        <form onSubmit={registerCreative} className="space-y-4">
          <Field label="Креатив" hint="Показаны креативы без erid">
            <select required className={inputCls} value={crForm.creative_id} onChange={(e) => setCrForm({ ...crForm, creative_id: e.target.value })}>
              <option value="">— выбрать —</option>
              {unregisteredCreatives.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </Field>
          <Field label="Договор" hint="Только договоры, зарегистрированные в ОРД. Пусто — подобрать по контрагенту креатива">
            <select className={inputCls} value={crForm.contract_id} onChange={(e) => setCrForm({ ...crForm, contract_id: e.target.value })}>
              <option value="">Автоматически по контрагенту</option>
              {registeredContracts.map((ct) => (
                <option key={ct.id} value={ct.id}>{ct.number} ({ct.client_name})</option>
              ))}
            </select>
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setCrOpen(false)}>Отмена</Button>
            <Button type="submit" disabled={crSaving}>{crSaving ? "Регистрация…" : "Получить erid"}</Button>
          </div>
        </form>
      </Modal>
    </main>
  )
}
