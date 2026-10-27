"use client"

import { useMemo, useState } from "react"
import useSWR, { mutate } from "swr"
import { api, fetcher } from "@/lib/api"
import { Button, Field, inputCls, Modal, Spinner } from "@/components/ui"
import { ForeignRequisitesFields } from "@/components/foreign-requisites-fields"
import { EMPTY_FOREIGN, foreignPayload, foreignRequisitesError } from "@/lib/oksm"
import type { Counterparty, OrdContract } from "@/lib/types"

/**
 * Данные ОРД кампании (B13d, ord_spec §7 D2) — поток как в Avito:
 * «кого рекламируете» → рекламодатель (или добавить) → договоры (или добавить)
 * → экран «Проверьте данные» → «Всё верно». Валидация — на бэкенде
 * (POST /campaigns/ord-chain/preview или GET /campaigns/{id}/ord-chain).
 */

export type OrdMode = "self" | "own_client" | "partner_client"

export interface OrdChainValues {
  ord_mode: OrdMode | null
  counterparty_id: string | null
  initial_contract_id: string | null
  executive_contract_id: string | null
}

export const EMPTY_CHAIN: OrdChainValues = {
  ord_mode: null,
  counterparty_id: null,
  initial_contract_id: null,
  executive_contract_id: null,
}

interface PartyView { id: string; name: string | null; inn: string | null }
interface ContractView {
  id: string
  number: string | null
  signed_at: string | null
  contract_type: string | null
  ord_status: string
  client: PartyView
  contractor: PartyView
}
export interface OrdChainSummary {
  campaign_id: string | null
  mode: OrdMode | null
  mode_label: string | null
  advertiser: (PartyView & { ord_status: string; is_self: boolean; legal_type: string | null }) | null
  initial_contract: ContractView | null
  executive_contract: ContractView | null
  self_org: (PartyView & { ord_status: string }) | null
  registration_contract_id: string | null
  problems: string[]
  problem_labels: string[]
  ready: boolean
  confirmed: boolean
  confirmed_at: string | null
}

const MODES: { key: OrdMode; title: string; desc: string }[] = [
  { key: "self", title: "Свои товары и услуги", desc: "Вы — рекламодатель. Один договор: между вами и рекламной системой Central DSP." },
  { key: "own_client", title: "Своего клиента", desc: "Прямой договор с рекламодателем. Цепочка: рекламодатель → вы (агентство) → Central DSP." },
  { key: "partner_client", title: "Клиента партнёра", desc: "Рекламодатель пришёл через посредника. Между договорами может быть посреднический." },
]

const STATUS_LABEL: Record<string, string> = {
  registered: "в ОРД",
  draft: "не отправлен",
  sending: "отправляется",
  failed: "ошибка",
  pending: "ожидает",
  error: "ошибка",
}

function partyLabel(cp: Pick<Counterparty, "name" | "inn"> | PartyView | null | undefined) {
  if (!cp) return "—"
  return `${cp.name || "—"}${cp.inn ? ` · ИНН ${cp.inn}` : ""}`
}

function contractLabel(c: OrdContract) {
  return `№ ${c.number || "б/н"}${c.signed_at ? ` от ${c.signed_at}` : ""} · ${c.client_name || "?"} → ${c.contractor_name || "?"}`
}

function StatusDot({ status }: { status: string | undefined }) {
  const ok = status === "registered"
  return (
    <span className={`inline-flex items-center gap-1 text-xs ${ok ? "text-emerald-700" : "text-amber-700"}`}>
      <span aria-hidden="true" className={`inline-block h-1.5 w-1.5 rounded-full ${ok ? "bg-emerald-500" : "bg-amber-500"}`} />
      {STATUS_LABEL[status || ""] || status || "—"}
    </span>
  )
}

// ---------------------------------------------------------------- forms ---

function CounterpartyModal({
  open, onClose, onCreated, role, title, advertiserId,
}: {
  open: boolean; onClose: () => void; onCreated: (cp: Counterparty) => void; role: "advertiser" | "agency"; title: string
  /** п.3: новое юрлицо рекламодателя сразу привязывается к выбранному аккаунту */
  advertiserId?: string | null
}) {
  const [form, setForm] = useState({ name: "", inn: "", ogrn: "", legal_type: "ul", address: "" })
  const [foreign, setForeign] = useState(EMPTY_FOREIGN)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    const foreignError = form.legal_type === "foreign" ? foreignRequisitesError(foreign) : null
    if (foreignError) {
      setError(foreignError)
      return
    }
    setSaving(true)
    setError(null)
    try {
      const cp = await api<Counterparty>("/ord/counterparties", {
        method: "POST",
        body: { ...form, ...foreignPayload(form.legal_type, foreign), role, ...(advertiserId ? { advertiser_id: advertiserId } : {}) },
      })
      await mutate("/ord/counterparties")
      setForm({ name: "", inn: "", ogrn: "", legal_type: "ul", address: "" })
      setForeign(EMPTY_FOREIGN)
      onCreated(cp)
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка сохранения")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={title} subtitle="Данные из ЕГРЮЛ/ЕГРИП. После сохранения зарегистрируйте в ОРД — без этого цепочку нельзя подтвердить.">
      <form onSubmit={submit} className="space-y-4">
        <Field label="Название">
          <input required className={inputCls} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="ООО Ромашка" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="ИНН" hint="10 цифр для юрлица, 12 для ИП/физлица">
            <input required minLength={5} className={inputCls} value={form.inn} onChange={(e) => setForm({ ...form, inn: e.target.value })} placeholder="7700000000" />
          </Field>
          <Field label="Тип">
            <select className={inputCls} value={form.legal_type} onChange={(e) => setForm({ ...form, legal_type: e.target.value })}>
              <option value="ul">Юрлицо</option>
              <option value="ip">ИП</option>
              <option value="fl">Физлицо</option>
              <option value="foreign">Иностранное</option>
            </select>
          </Field>
        </div>
        {form.legal_type === "foreign" ? <ForeignRequisitesFields value={foreign} onChange={setForeign} /> : null}
        <Field label="ОГРН / ОГРНИП">
          <input className={inputCls} value={form.ogrn} onChange={(e) => setForm({ ...form, ogrn: e.target.value })} placeholder="1027700000000" />
        </Field>
        <Field label="Юридический адрес" hint="Требуется для регистрации в ОРД">
          <input className={inputCls} value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} placeholder="г. Москва, ул. Ленина, д. 1" />
        </Field>
        {error ? <p role="alert" className="text-sm text-red-600">{error}</p> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>Отмена</Button>
          <Button type="submit" disabled={saving}>{saving ? "Сохранение…" : "Сохранить"}</Button>
        </div>
      </form>
    </Modal>
  )
}

function ContractModal({
  open, onClose, onCreated, title, client, contractorOptions, fixedContractor, parentOptions,
}: {
  open: boolean
  onClose: () => void
  onCreated: (c: OrdContract) => void
  title: string
  client: Counterparty | null
  /** Кандидаты в исполнители (когда исполнитель не фиксирован) */
  contractorOptions: Counterparty[]
  /** Фиксированный исполнитель (например, наша организация) */
  fixedContractor?: Counterparty | null
  /** Посреднические договоры для parent_contract_id (partner_client) */
  parentOptions?: OrdContract[]
}) {
  const [form, setForm] = useState({ contractor_id: "", number: "", signed_at: "", contract_type: "service", parent_contract_id: "" })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!client) return
    setSaving(true)
    setError(null)
    try {
      const c = await api<OrdContract>("/ord/contracts", {
        method: "POST",
        body: {
          client_id: client.id,
          contractor_id: fixedContractor ? fixedContractor.id : form.contractor_id,
          number: form.number,
          signed_at: form.signed_at,
          contract_type: form.contract_type,
          parent_contract_id: form.parent_contract_id || null,
        },
      })
      await mutate("/ord/contracts")
      setForm({ contractor_id: "", number: "", signed_at: "", contract_type: "service", parent_contract_id: "" })
      onCreated(c)
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка сохранения")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={title} subtitle="Договор нужно зарегистрировать в ОРД до подтверждения цепочки.">
      <form onSubmit={submit} className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Заказчик">
            <input className={inputCls} disabled value={partyLabel(client)} />
          </Field>
          <Field label="Исполнитель">
            {fixedContractor ? (
              <input className={inputCls} disabled value={partyLabel(fixedContractor)} />
            ) : (
              <select required className={inputCls} value={form.contractor_id} onChange={(e) => setForm({ ...form, contractor_id: e.target.value })}>
                <option value="">— выбрать —</option>
                {contractorOptions.map((cp) => (
                  <option key={cp.id} value={cp.id}>{partyLabel(cp)}</option>
                ))}
              </select>
            )}
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Номер договора">
            <input required className={inputCls} value={form.number} onChange={(e) => setForm({ ...form, number: e.target.value })} placeholder="Д-2026/01" />
          </Field>
          <Field label="Дата подписания">
            <input required type="date" className={inputCls} value={form.signed_at} onChange={(e) => setForm({ ...form, signed_at: e.target.value })} />
          </Field>
        </div>
        <Field label="Тип договора">
          <select className={inputCls} value={form.contract_type} onChange={(e) => setForm({ ...form, contract_type: e.target.value })}>
            <option value="service">Оказание услуг</option>
            <option value="mediation">Посреднический</option>
            <option value="additional">Доп. соглашение</option>
          </select>
        </Field>
        {parentOptions && parentOptions.length ? (
          <Field label="Посреднический договор (родительский)" hint="Если между изначальным и этим договором стоит посредник">
            <select className={inputCls} value={form.parent_contract_id} onChange={(e) => setForm({ ...form, parent_contract_id: e.target.value })}>
              <option value="">— нет —</option>
              {parentOptions.map((c) => (
                <option key={c.id} value={c.id}>{contractLabel(c)}</option>
              ))}
            </select>
          </Field>
        ) : null}
        {error ? <p role="alert" className="text-sm text-red-600">{error}</p> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>Отмена</Button>
          <Button type="submit" disabled={saving || !client}>{saving ? "Сохранение…" : "Сохранить"}</Button>
        </div>
      </form>
    </Modal>
  )
}

// ------------------------------------------------------------ step: edit ---

function ContractPicker({
  label, hint, value, onChange, options, onAdd, addDisabledReason,
}: {
  label: string
  hint?: string
  value: string | null
  onChange: (id: string | null) => void
  options: OrdContract[]
  onAdd: () => void
  addDisabledReason?: string | null
}) {
  const selected = options.find((c) => c.id === value)
  return (
    <div className="space-y-1.5">
      <Field label={label} hint={hint}>
        <select className={inputCls} value={value || ""} onChange={(e) => onChange(e.target.value || null)}>
          <option value="">— выбрать договор —</option>
          {options.map((c) => (
            <option key={c.id} value={c.id}>{contractLabel(c)}</option>
          ))}
        </select>
      </Field>
      <div className="flex items-center justify-between gap-3">
        {selected ? <StatusDot status={selected.ord_status} /> : <span />}
        <button
          type="button"
          onClick={onAdd}
          disabled={!!addDisabledReason}
          title={addDisabledReason || undefined}
          className="text-xs font-medium text-accent underline-offset-2 hover:underline disabled:cursor-not-allowed disabled:opacity-50"
        >
          + Добавить договор
        </button>
      </div>
    </div>
  )
}

export function OrdChainEditor({
  value, onChange, disabled, advertiserId,
}: {
  value: OrdChainValues; onChange: (v: OrdChainValues) => void; disabled?: boolean
  /** п.3: аккаунт рекламодателя DSP — его юрлица ОРД показываются первыми */
  advertiserId?: string | null
}) {
  const { data: counterparties } = useSWR<Counterparty[]>("/ord/counterparties", fetcher)
  const { data: contracts } = useSWR<OrdContract[]>("/ord/contracts", fetcher)

  const selfOrg = useMemo(() => (counterparties || []).find((cp) => cp.is_self) || null, [counterparties])
  const parties = useMemo(() => (counterparties || []).filter((cp) => !cp.is_self), [counterparties])
  const advertiser = parties.find((cp) => cp.id === value.counterparty_id) || null
  const linkedParties = useMemo(
    () => (advertiserId ? parties.filter((cp) => cp.advertiser_id === advertiserId) : []),
    [parties, advertiserId],
  )
  const otherParties = useMemo(
    () => parties.filter((cp) => !linkedParties.includes(cp)),
    [parties, linkedParties],
  )
  const initial = (contracts || []).find((c) => c.id === value.initial_contract_id) || null

  const initialOptions = useMemo(
    () => (contracts || []).filter((c) => advertiser && c.client_id === advertiser.id && (!selfOrg || c.contractor_id !== selfOrg.id)),
    [contracts, advertiser, selfOrg],
  )
  const directOptions = useMemo(
    () => (contracts || []).filter((c) => advertiser && c.client_id === advertiser.id && selfOrg && c.contractor_id === selfOrg.id),
    [contracts, advertiser, selfOrg],
  )
  const executiveOptions = useMemo(
    () => (contracts || []).filter((c) => selfOrg && c.contractor_id === selfOrg.id && (
      value.ord_mode === "partner_client" ? true : !initial || c.client_id === initial.contractor_id
    )),
    [contracts, selfOrg, initial, value.ord_mode],
  )
  const mediationOptions = useMemo(
    () => (contracts || []).filter((c) => c.contract_type === "mediation"),
    [contracts],
  )
  const executiveClient = useMemo(() => {
    if (value.ord_mode === "self") return advertiser
    if (!initial) return null
    return (counterparties || []).find((cp) => cp.id === initial.contractor_id) || null
  }, [value.ord_mode, advertiser, initial, counterparties])

  const [cpModal, setCpModal] = useState<"advertiser" | "agency" | null>(null)
  const [ctModal, setCtModal] = useState<"initial" | "executive" | null>(null)
  const [agencyForInitial, setAgencyForInitial] = useState<Counterparty | null>(null)

  const setMode = (m: OrdMode) =>
    onChange({
      ...value,
      ord_mode: m,
      // единственное юрлицо, привязанное к рекламодателю, подставляем сразу
      counterparty_id: value.counterparty_id ?? (linkedParties.length === 1 ? linkedParties[0].id : null),
      initial_contract_id: m === "self" ? null : value.initial_contract_id,
      executive_contract_id: null,
    })

  if (!counterparties || !contracts) return <Spinner />

  return (
    <fieldset disabled={disabled} className="space-y-6 p-4">
      <div>
        <p className="text-sm font-semibold text-foreground">Кого вы рекламируете?</p>
        <p className="mt-0.5 text-xs text-muted">От ответа зависит, какие договоры уйдут в ОРД вместе с креативами (38-ФЗ).</p>
        <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-3" role="radiogroup" aria-label="Кого вы рекламируете">
          {MODES.map((m) => (
            <button
              key={m.key}
              type="button"
              role="radio"
              aria-checked={value.ord_mode === m.key}
              onClick={() => setMode(m.key)}
              className={`rounded-2xl border p-4 text-left transition-colors ${
                value.ord_mode === m.key ? "border-foreground bg-panel" : "border-border hover:bg-panel"
              }`}
            >
              <p className="text-sm font-semibold text-foreground">{m.title}</p>
              <p className="mt-1 text-xs leading-relaxed text-muted">{m.desc}</p>
            </button>
          ))}
        </div>
      </div>

      {value.ord_mode ? (
        <div className="space-y-1.5">
          <Field label="Рекламодатель" hint="Юрлицо, чьи товары или услуги рекламируются. Показывается в инфо-значке креатива.">
            <select
              className={inputCls}
              value={value.counterparty_id || ""}
              onChange={(e) => onChange({ ...value, counterparty_id: e.target.value || null, initial_contract_id: null, executive_contract_id: null })}
            >
              <option value="">— выбрать рекламодателя —</option>
              {linkedParties.length ? (
                <>
                  <optgroup label="Юрлица этого рекламодателя">
                    {linkedParties.map((cp) => (
                      <option key={cp.id} value={cp.id}>{partyLabel(cp)}</option>
                    ))}
                  </optgroup>
                  {otherParties.length ? (
                    <optgroup label="Другие юрлица">
                      {otherParties.map((cp) => (
                        <option key={cp.id} value={cp.id}>{partyLabel(cp)}</option>
                      ))}
                    </optgroup>
                  ) : null}
                </>
              ) : (
                parties.map((cp) => (
                  <option key={cp.id} value={cp.id}>{partyLabel(cp)}</option>
                ))
              )}
            </select>
          </Field>
          {advertiserId && !linkedParties.length ? (
            <p className="text-xs text-muted">
              У выбранного рекламодателя пока нет юрлица для ОРД. Выберите из списка или добавьте новое — оно привяжется к рекламодателю.
            </p>
          ) : null}
          <div className="flex items-center justify-between gap-3">
            {advertiser ? <StatusDot status={advertiser.ord_status} /> : <span />}
            <button type="button" onClick={() => setCpModal("advertiser")} className="text-xs font-medium text-accent underline-offset-2 hover:underline">
              + Добавить рекламодателя
            </button>
          </div>
        </div>
      ) : null}

      {value.ord_mode && advertiser && !selfOrg ? (
        <p role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Не задана наша организация (ОРС). Администратор должен отметить её в разделе «Маркировка (ОРД)» — без неё исполнительский договор не построить.
        </p>
      ) : null}

      {value.ord_mode === "self" && advertiser ? (
        <ContractPicker
          label="Договор с рекламной системой"
          hint={`Заказчик — ${advertiser.name}, исполнитель — ${selfOrg?.name || "наша организация"}`}
          value={value.executive_contract_id}
          onChange={(id) => onChange({ ...value, executive_contract_id: id })}
          options={directOptions}
          onAdd={() => setCtModal("executive")}
          addDisabledReason={selfOrg ? null : "Сначала задайте нашу организацию (ОРС)"}
        />
      ) : null}

      {(value.ord_mode === "own_client" || value.ord_mode === "partner_client") && advertiser ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <ContractPicker
            label="Изначальный договор"
            hint={`Между рекламодателем (${advertiser.name}) и вами / агентством`}
            value={value.initial_contract_id}
            onChange={(id) => onChange({ ...value, initial_contract_id: id, executive_contract_id: null })}
            options={initialOptions}
            onAdd={() => setCtModal("initial")}
          />
          <ContractPicker
            label="Исполнительский договор"
            hint={executiveClient ? `Между ${executiveClient.name} и ${selfOrg?.name || "нашей организацией"}` : "Сначала выберите изначальный договор"}
            value={value.executive_contract_id}
            onChange={(id) => onChange({ ...value, executive_contract_id: id })}
            options={executiveOptions}
            onAdd={() => setCtModal("executive")}
            addDisabledReason={!initial ? "Сначала выберите изначальный договор" : !selfOrg ? "Сначала задайте нашу организацию (ОРС)" : null}
          />
        </div>
      ) : null}

      <CounterpartyModal
        open={cpModal === "advertiser"}
        onClose={() => setCpModal(null)}
        role="advertiser"
        title="Новый рекламодатель"
        advertiserId={advertiserId}
        onCreated={(cp) => onChange({ ...value, counterparty_id: cp.id, initial_contract_id: null, executive_contract_id: null })}
      />
      <CounterpartyModal
        open={cpModal === "agency"}
        onClose={() => setCpModal(null)}
        role="agency"
        title="Новое агентство (исполнитель по изначальному договору)"
        onCreated={(cp) => { setAgencyForInitial(cp); setCtModal("initial") }}
      />
      <ContractModal
        open={ctModal === "initial"}
        onClose={() => setCtModal(null)}
        title="Изначальный договор"
        client={advertiser}
        contractorOptions={agencyForInitial ? [agencyForInitial, ...parties.filter((p) => p.id !== agencyForInitial.id && p.id !== advertiser?.id)] : parties.filter((p) => p.id !== advertiser?.id)}
        onCreated={(c) => onChange({ ...value, initial_contract_id: c.id, executive_contract_id: null })}
      />
      <ContractModal
        open={ctModal === "executive"}
        onClose={() => setCtModal(null)}
        title={value.ord_mode === "self" ? "Договор с рекламной системой" : "Исполнительский договор"}
        client={executiveClient}
        contractorOptions={[]}
        fixedContractor={selfOrg}
        parentOptions={value.ord_mode === "partner_client" ? mediationOptions : undefined}
        onCreated={(c) => onChange({ ...value, executive_contract_id: c.id })}
      />
      {ctModal === "initial" && !parties.some((p) => p.id !== advertiser?.id) ? (
        <p className="text-xs text-muted">
          Нет ни одного агентства-исполнителя.{" "}
          <button type="button" className="font-medium text-accent hover:underline" onClick={() => { setCtModal(null); setCpModal("agency") }}>
            Добавить агентство
          </button>
        </p>
      ) : null}
    </fieldset>
  )
}

// --------------------------------------------------------- step: confirm ---

function ChainRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-1 py-2.5 sm:grid-cols-[11rem_1fr] sm:gap-4">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="text-sm text-foreground">{children}</dd>
    </div>
  )
}

function ContractLine({ c }: { c: ContractView | null }) {
  if (!c) return <span className="text-muted">не выбран</span>
  return (
    <span className="flex flex-wrap items-baseline gap-x-2">
      <span>№ {c.number || "б/н"}{c.signed_at ? ` от ${c.signed_at}` : ""}</span>
      <span className="text-xs text-muted">{c.client.name} → {c.contractor.name}</span>
      <StatusDot status={c.ord_status} />
    </span>
  )
}

export function OrdChainSummaryView({ summary }: { summary: OrdChainSummary }) {
  return (
    <div className="p-4">
      <dl className="divide-y divide-border">
        <ChainRow label="Кого рекламируете">{summary.mode_label || <span className="text-muted">не выбрано</span>}</ChainRow>
        <ChainRow label="Рекламодатель">
          {summary.advertiser ? (
            <span className="flex flex-wrap items-baseline gap-x-2">
              <span>{partyLabel(summary.advertiser)}</span>
              <StatusDot status={summary.advertiser.ord_status} />
            </span>
          ) : <span className="text-muted">не выбран</span>}
        </ChainRow>
        {summary.mode !== "self" ? (
          <ChainRow label="Изначальный договор"><ContractLine c={summary.initial_contract} /></ChainRow>
        ) : null}
        <ChainRow label={summary.mode === "self" ? "Договор с рекламной системой" : "Исполнительский договор"}>
          <ContractLine c={summary.executive_contract} />
        </ChainRow>
        <ChainRow label="Рекламная система (ОРС)">
          {summary.self_org ? partyLabel(summary.self_org) : <span className="text-amber-700">не задана</span>}
        </ChainRow>
      </dl>
      {summary.problem_labels.length ? (
        <ul role="alert" className="mt-3 space-y-1 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
          {summary.problem_labels.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
          Цепочка полная, все участники зарегистрированы в ОРД. После подтверждения данные нельзя изменить — для другого рекламодателя создайте новую кампанию.
        </p>
      )}
    </div>
  )
}

/** Кнопка/статус «Всё верно» для уже созданной кампании (страница кампании). */
export function OrdChainCampaignCard({
  campaignId, values, onValuesChange, canEdit,
}: { campaignId: string; values: OrdChainValues; onValuesChange: (v: OrdChainValues) => Promise<void>; canEdit: boolean }) {
  const { data: summary, mutate: refresh } = useSWR<OrdChainSummary>(`/campaigns/${campaignId}/ord-chain`, fetcher)
  const [confirming, setConfirming] = useState(false)
  const [review, setReview] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const confirm = async () => {
    setConfirming(true)
    setError(null)
    try {
      await api(`/campaigns/${campaignId}/ord-chain/confirm`, { method: "POST" })
      await refresh()
      await mutate(`/campaigns/${campaignId}/diagnostics`)
      setReview(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось подтвердить")
    } finally {
      setConfirming(false)
    }
  }

  if (!summary) return <div className="p-4"><Spinner /></div>

  if (summary.confirmed) {
    return (
      <div>
        <OrdChainSummaryView summary={summary} />
        <p className="border-t border-border px-4 py-3 text-xs text-muted">
          Данные ОРД подтверждены {summary.confirmed_at ? new Date(summary.confirmed_at).toLocaleString("ru-RU") : ""} и заморожены: на них регистрируются erid креативов.
        </p>
      </div>
    )
  }

  return (
    <div>
      {review ? (
        <>
          <OrdChainSummaryView summary={summary} />
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border px-4 py-3">
            {error ? <p role="alert" className="mr-auto text-sm text-red-600">{error}</p> : null}
            <Button type="button" variant="secondary" onClick={() => setReview(false)}>Изменить</Button>
            <Button type="button" onClick={confirm} disabled={!summary.ready || confirming}>
              {confirming ? "Подтверждение…" : "Всё верно, подтвердить"}
            </Button>
          </div>
        </>
      ) : (
        <>
          <OrdChainEditor
            value={values}
            disabled={!canEdit}
            onChange={async (v) => {
              await onValuesChange(v)
              await refresh()
            }}
          />
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border px-4 py-3">
            {summary.problem_labels.length && values.ord_mode ? (
              <p className="mr-auto text-xs text-muted">{summary.problem_labels[0]}{summary.problem_labels.length > 1 ? ` (+${summary.problem_labels.length - 1})` : ""}</p>
            ) : null}
            <Button type="button" onClick={() => setReview(true)} disabled={!values.ord_mode || !canEdit}>
              Проверить данные →
            </Button>
          </div>
        </>
      )}
    </div>
  )
}
