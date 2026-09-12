"use client"

import { useEffect, useMemo, useState } from "react"
import useSWR, { mutate } from "swr"

import { ORD_NOTIFICATIONS_KEY } from "@/components/ord-notifications"
import { api, describeApiError, fetcher } from "@/lib/api"
import { fmtMoney } from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Field, inputCls, Modal, Spinner, StatusBadge, Table } from "@/components/ui"

/**
 * Вкладка «Акты» раздела /ord (B13f, ОРС-модель — ord_spec.md §7).
 * Месяц → таблица по исполнительским договорам (заказчик → ОРС): факт по
 * статистике, акт за месяц (если есть) → редактор акта: номер, период, сумма
 * с НДС, ставка, дата, разбивка по изначальным договорам, строки с построчным
 * статусом ОРД → «Отправить в ОРД» / повтор.
 */

export interface OrdContractLite {
  id: string
  number: string
  client_id: string
  contractor_id: string
  client_name: string
  contractor_name: string
  ord_status: string
  is_vat?: boolean | null
}

export interface CounterpartyLite {
  id: string
  name: string
  role: string
  is_self?: boolean
}

interface InvoiceRow {
  id: string
  contract_id: string
  contract_number: string
  number: string
  status: string
  invoice_date: string
  period_start: string
  period_end: string
  amount_gross: number | string
  amount_net?: number | string
  vat_rate?: number
  items_count: number
  items_accepted: number
  items_rejected: number
  error?: string | null
  ord_external_id?: string | null
  submitted_at?: string | null
}

interface InvoiceItem {
  creative_id: string
  creative_name?: string | null
  contract_id: string
  version: number
  erid?: string | null
  period: string
  payment_type?: string | null
  fact_impressions: number
  fact_clicks: number
  fact_spend: string | number
  impressions: number
  clicks: number
  spend: string | number
  unit_cost?: string | number | null
  status?: string
  error?: string | null
  in_invoice?: { id: string; number: string; status: string } | null
}

interface ContractSplit {
  id?: string
  initial_contract_id?: string
  number: string
  ord_status?: string
  ord_external_id?: string | null
  client?: { name: string; inn?: string }
  amount_gross: string | number
  amount_net?: string | number
  items_count?: number
}

interface Warnings {
  already_invoiced?: { external_statistic_id: string; number: string }[]
  without_erid?: { creative_id: string; name: string; impressions: number }[]
  contracts_unregistered?: string[]
  contract_not_executive?: boolean
  amount_diff?: { invoice: string; statistics: string; pct: number }
}

interface Preview {
  contract_id: string
  period_start: string
  period_end: string
  client_role: string
  contractor_role: string
  is_vat: boolean
  vat_rate: number
  spend_multiplier: number
  items: InvoiceItem[]
  contracts: ContractSplit[]
  amount_gross: string
  amount_net: string
  statistics_amount_gross: string
  statistics_amount_net: string
  warnings: Warnings
}

interface InvoiceFull extends InvoiceRow {
  is_vat: boolean
  client_role?: string
  contractor_role?: string
  items: InvoiceItem[]
  contracts: ContractSplit[]
  statistics_amount_gross?: string | number
  statistics_amount_net?: string | number
  amount_diff_pct?: number | null
  warnings?: Warnings
}

const num = (v: string | number | null | undefined) => (v == null || v === "" ? 0 : Number(v))
const rub = (v: string | number | null | undefined) => fmtMoney(num(v))
const pad = (n: number) => String(n).padStart(2, "0")

/** Предыдущий календарный месяц — акт обычно закрывают за прошедший период. */
function defaultMonth(): string {
  const d = new Date()
  d.setDate(1)
  d.setMonth(d.getMonth() - 1)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`
}

function monthBounds(month: string): { start: string; end: string } {
  const [y, m] = month.split("-").map(Number)
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate()
  return { start: `${month}-01`, end: `${month}-${pad(last)}` }
}

function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number)
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("ru-RU", { month: "long", year: "numeric", timeZone: "UTC" })
}

const overlaps = (inv: InvoiceRow, start: string, end: string) =>
  String(inv.period_start).slice(0, 10) <= end && String(inv.period_end).slice(0, 10) >= start

const VAT_OPTIONS = [22, 20, 10, 0]

export function OrdInvoicesTab({
  contracts,
  counterparties,
  canWrite,
}: {
  contracts: OrdContractLite[] | undefined
  counterparties: CounterpartyLite[] | undefined
  canWrite: boolean
}) {
  const [month, setMonth] = useState(defaultMonth)
  const { data: invoices } = useSWR<InvoiceRow[]>("/ord/invoices", fetcher)
  const [editor, setEditor] = useState<{ contract: OrdContractLite; invoiceId?: string } | null>(null)
  const [bulk, setBulk] = useState<{ running: boolean; text: string | null }>({ running: false, text: null })

  const { start, end } = monthBounds(month)
  const selfIds = useMemo(
    () => new Set((counterparties || []).filter((c) => c.is_self || c.role === "ors").map((c) => c.id)),
    [counterparties],
  )
  // Исполнительские договоры: исполнитель — наша организация (ОРС).
  const executive = useMemo(() => (contracts || []).filter((c) => selfIds.has(c.contractor_id)), [contracts, selfIds])
  const byId = useMemo(() => new Map((contracts || []).map((c) => [c.id, c])), [contracts])
  const executiveIds = useMemo(() => new Set(executive.map((c) => c.id)), [executive])
  const monthDrafts = useMemo(
    () => (invoices || []).filter((i) => executiveIds.has(i.contract_id) && overlaps(i, start, end) && ["draft", "failed", "partial", "erir_error"].includes(i.status)),
    [invoices, executiveIds, start, end],
  )

  const submitAll = async () => {
    if (monthDrafts.length === 0 || !confirm(`Отправить в ОРД ${monthDrafts.length} акт(ов) за ${monthLabel(month)}?`)) return
    setBulk({ running: true, text: null })
    let ok = 0
    const failed: string[] = []
    for (const inv of monthDrafts) {
      try {
        const res = await api<{ status: string; error?: string | null }>(`/ord/invoices/${inv.id}/submit`, { method: "POST" })
        if (res.status === "ok") ok += 1
        else failed.push(`${inv.number}: ${res.status === "partial" ? "часть строк отклонена" : res.error || "отклонён"}`)
      } catch (err) {
        failed.push(`${inv.number}: ${describeApiError(err)}`)
      }
    }
    await Promise.all([mutate("/ord/invoices"), mutate(ORD_NOTIFICATIONS_KEY)])
    setBulk({ running: false, text: `Принято: ${ok}${failed.length ? `. Ошибки — ${failed.join("; ")}` : ""}` })
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Акты по исполнительским договорам"
          subtitle="Акт закрывает месяц по договору «заказчик → Central DSP (ОРС)». Сумма делится по изначальным договорам кампаний, строки — по креативам и версиям erid. В ОРД уходит invoice с contracts[] и статистика по месяцам."
          action={
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2 text-sm text-muted">
                Месяц
                <input
                  type="month"
                  className={`${inputCls} w-auto`}
                  value={month}
                  onChange={(e) => e.target.value && setMonth(e.target.value)}
                  aria-label="Месяц акта"
                />
              </label>
              {canWrite && monthDrafts.length > 0 ? (
                <Button variant="secondary" disabled={bulk.running} onClick={submitAll}>
                  {bulk.running ? "Отправка…" : `Отправить все в ОРД (${monthDrafts.length})`}
                </Button>
              ) : null}
            </div>
          }
        />
        {bulk.text ? <p role="status" className="mb-3 text-sm text-muted">{bulk.text}</p> : null}
        {!contracts || !counterparties || !invoices ? (
          <Spinner />
        ) : executive.length === 0 ? (
          <EmptyState
            title="Нет исполнительских договоров"
            description="Добавьте договор, где исполнитель — наша организация (флаг «наша» у контрагента на вкладке «Контрагенты»), и зарегистрируйте его в ОРД."
          />
        ) : (
          <Table headers={["Заказчик", "Договор", `Статистика, ${monthLabel(month)}`, "Акт", "Статус", ""]}>
            {executive.map((ct) => (
              <ContractMonthRow
                key={ct.id}
                contract={ct}
                start={start}
                end={end}
                invoices={invoices.filter((i) => i.contract_id === ct.id && overlaps(i, start, end))}
                canWrite={canWrite}
                onOpen={(invoiceId) => setEditor({ contract: ct, invoiceId })}
              />
            ))}
          </Table>
        )}
      </Card>

      <Card>
        <CardHeader title="Все акты" subtitle="Черновики, отправленные и частично принятые акты за любые периоды" />
        {!invoices ? (
          <Spinner />
        ) : invoices.length === 0 ? (
          <EmptyState title="Актов ещё нет" description="Выберите месяц и нажмите «Создать акт» напротив договора" />
        ) : (
          <Table headers={["Номер", "Договор", "Период", "Сумма с НДС", "Строк", "Статус", ""]}>
            {invoices.map((inv) => {
              const ct = byId.get(inv.contract_id)
              return (
                <tr key={inv.id}>
                  <td className="px-4 py-3 font-medium text-foreground">{inv.number}</td>
                  <td className="px-4 py-3 text-muted">
                    {inv.contract_number}
                    {ct ? <span className="block text-xs">{ct.client_name}</span> : null}
                  </td>
                  <td className="px-4 py-3 text-muted">
                    {String(inv.period_start).slice(0, 10)} — {String(inv.period_end).slice(0, 10)}
                  </td>
                  <td className="px-4 py-3 text-foreground">{rub(inv.amount_gross)}</td>
                  <td className="px-4 py-3 text-muted">
                    {inv.items_count}
                    {inv.items_rejected > 0 ? <span className="ml-1 text-danger">({inv.items_rejected} откл.)</span> : null}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={inv.status} />
                    {inv.error ? <p className="mt-1 max-w-xs text-xs text-danger">{inv.error}</p> : null}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {ct ? (
                      <Button variant="secondary" onClick={() => setEditor({ contract: ct, invoiceId: inv.id })}>
                        Открыть
                      </Button>
                    ) : null}
                  </td>
                </tr>
              )
            })}
          </Table>
        )}
      </Card>

      {editor ? (
        <InvoiceEditor
          contract={editor.contract}
          invoiceId={editor.invoiceId}
          month={month}
          canWrite={canWrite}
          onClose={() => setEditor(null)}
        />
      ) : null}
    </div>
  )
}

function ContractMonthRow({
  contract,
  start,
  end,
  invoices,
  canWrite,
  onOpen,
}: {
  contract: OrdContractLite
  start: string
  end: string
  invoices: InvoiceRow[]
  canWrite: boolean
  onOpen: (invoiceId?: string) => void
}) {
  const { data: preview, error } = useSWR<Preview>(
    `/ord/invoices/preview?contract_id=${contract.id}&period_start=${start}&period_end=${end}`,
    fetcher,
  )
  const registered = contract.ord_status === "registered"
  return (
    <tr>
      <td className="px-4 py-3 font-medium text-foreground">{contract.client_name}</td>
      <td className="px-4 py-3 text-muted">
        {contract.number}
        {!registered ? <span className="ml-2 text-xs text-danger">не в ОРД</span> : null}
      </td>
      <td className="px-4 py-3 text-foreground">
        {error ? (
          <span className="text-xs text-danger">{error instanceof Error ? error.message : "ошибка"}</span>
        ) : !preview ? (
          <span className="text-xs text-muted">…</span>
        ) : (
          <>
            {rub(preview.statistics_amount_gross)}
            <span className="block text-xs text-muted">
              {preview.items.length} строк · {preview.contracts.length} изнач. договор(ов)
              {preview.warnings.without_erid?.length ? (
                <span className="ml-1 text-danger">· без erid: {preview.warnings.without_erid.length}</span>
              ) : null}
            </span>
          </>
        )}
      </td>
      <td className="px-4 py-3 text-muted">
        {invoices.length === 0 ? "—" : invoices.map((i) => (
          <button key={i.id} type="button" className="block text-left text-foreground underline-offset-2 hover:underline" onClick={() => onOpen(i.id)}>
            {i.number} · {rub(i.amount_gross)}
          </button>
        ))}
      </td>
      <td className="px-4 py-3">
        {invoices.length === 0 ? <span className="text-xs text-muted">нет акта</span> : invoices.map((i) => (
          <div key={i.id} className="mb-1 last:mb-0"><StatusBadge status={i.status} /></div>
        ))}
      </td>
      <td className="px-4 py-3 text-right">
        {canWrite ? (
          <Button
            variant={invoices.length === 0 ? "primary" : "secondary"}
            disabled={!preview || preview.items.filter((it) => !it.in_invoice).length === 0}
            onClick={() => onOpen(undefined)}
          >
            Создать акт
          </Button>
        ) : null}
      </td>
    </tr>
  )
}

interface FormState {
  number: string
  invoice_date: string
  period_start: string
  period_end: string
  amount_gross: string
  vat_rate: number
  split: Record<string, string> // initial_contract_id → amount_gross
  splitManual: boolean
}

function InvoiceEditor({
  contract,
  invoiceId,
  month,
  canWrite,
  onClose,
}: {
  contract: OrdContractLite
  invoiceId?: string
  month: string
  canWrite: boolean
  onClose: () => void
}) {
  const bounds = monthBounds(month)
  const { data: invoice, mutate: reloadInvoice } = useSWR<InvoiceFull>(invoiceId ? `/ord/invoices/${invoiceId}` : null, fetcher)
  const [form, setForm] = useState<FormState | null>(null)
  const periodStart = form?.period_start ?? (invoice ? String(invoice.period_start).slice(0, 10) : bounds.start)
  const periodEnd = form?.period_end ?? (invoice ? String(invoice.period_end).slice(0, 10) : bounds.end)
  const previewKey =
    (!invoiceId || invoice) && periodStart && periodEnd
      ? `/ord/invoices/preview?contract_id=${contract.id}&period_start=${periodStart}&period_end=${periodEnd}${invoiceId ? `&invoice_id=${invoiceId}` : ""}`
      : null
  const { data: preview, error: previewError } = useSWR<Preview>(previewKey, fetcher)

  const [busy, setBusy] = useState<"save" | "submit" | "delete" | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // Форма заполняется один раз: из акта (редактирование) или из предпросмотра (новый).
  useEffect(() => {
    if (form) return
    if (invoiceId) {
      if (!invoice) return
      const split: Record<string, string> = {}
      for (const c of invoice.contracts || []) split[String(c.initial_contract_id ?? c.id)] = String(num(c.amount_gross).toFixed(2))
      setForm({
        number: invoice.number,
        invoice_date: String(invoice.invoice_date).slice(0, 10),
        period_start: String(invoice.period_start).slice(0, 10),
        period_end: String(invoice.period_end).slice(0, 10),
        amount_gross: num(invoice.amount_gross).toFixed(2),
        vat_rate: invoice.vat_rate ?? (invoice.is_vat ? 22 : 0),
        split,
        splitManual: false,
      })
    } else if (preview) {
      const today = new Date()
      setForm({
        number: "",
        invoice_date: `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`,
        period_start: preview.period_start,
        period_end: preview.period_end,
        amount_gross: preview.amount_gross,
        vat_rate: preview.vat_rate,
        split: {},
        splitManual: false,
      })
    }
  }, [form, invoiceId, invoice, preview])

  // Строки акта: у нового — из предпросмотра (без занятых другим актом),
  // у существующего — строки акта + статус ОРД, факт подтягивается из предпросмотра.
  const items: InvoiceItem[] = useMemo(() => {
    if (invoiceId) return invoice?.items ?? []
    return (preview?.items ?? []).filter((it) => !it.in_invoice)
  }, [invoiceId, invoice, preview])

  // Разбивка по изначальным договорам: пропорционально факту строк, если не правили вручную.
  const splitRows = useMemo(() => {
    const src: ContractSplit[] = invoiceId ? invoice?.contracts ?? [] : preview?.contracts ?? []
    const factByContract = new Map<string, number>()
    for (const it of items) {
      const k = String(it.contract_id)
      factByContract.set(k, (factByContract.get(k) ?? 0) + num(it.spend))
    }
    const total = Array.from(factByContract.values()).reduce((s, v) => s + v, 0)
    const amount = num(form?.amount_gross)
    return src.map((c) => {
      const id = String(c.initial_contract_id ?? c.id)
      const fact = factByContract.get(id) ?? 0
      const auto = total > 0 ? (amount * fact) / total : src.length ? amount / src.length : 0
      return { ...c, id, fact, auto: auto.toFixed(2), value: form?.splitManual ? form.split[id] ?? auto.toFixed(2) : auto.toFixed(2) }
    })
  }, [invoiceId, invoice, preview, items, form])

  const splitSum = splitRows.reduce((s, r) => s + num(r.value), 0)
  const splitMismatch = form?.splitManual && Math.abs(splitSum - num(form.amount_gross)) > 0.005
  const statsGross = num(invoiceId ? invoice?.statistics_amount_gross ?? preview?.statistics_amount_gross : preview?.statistics_amount_gross)
  const diffPct = statsGross > 0 && form ? Math.abs((num(form.amount_gross) - statsGross) / statsGross) * 100 : 0
  const net = form ? num(form.amount_gross) / (1 + form.vat_rate / 100) : 0
  const warnings: Warnings = { ...(preview?.warnings ?? {}), ...(invoiceId ? invoice?.warnings ?? {} : {}) }
  const locked = invoice?.status === "submitting"

  const body = () => {
    if (!form) return null
    return {
      contract_id: contract.id,
      number: form.number.trim(),
      invoice_date: form.invoice_date,
      period_start: form.period_start,
      period_end: form.period_end,
      amount_gross: num(form.amount_gross),
      vat_rate: form.vat_rate,
      is_vat: form.vat_rate > 0,
      contracts: form.splitManual ? splitRows.map((r) => ({ initial_contract_id: r.id, amount_gross: num(r.value) })) : [],
      items: items.map((it) => ({
        creative_id: it.creative_id,
        version: it.version,
        period: it.period,
        impressions: it.impressions,
        clicks: it.clicks,
        spend: num(it.spend),
        payment_type: it.payment_type ?? undefined,
      })),
    }
  }

  const describe = (err: unknown) => describeApiError(err)

  const save = async (): Promise<string | null> => {
    const b = body()
    if (!b) return null
    if (!b.number) {
      setError("Укажите номер акта")
      return null
    }
    if (b.items.length === 0) {
      setError("В акте нет строк: за период по договору нет показов с erid, либо они уже в другом акте")
      return null
    }
    const saved = await api<InvoiceFull>(invoiceId ? `/ord/invoices/${invoiceId}` : "/ord/invoices", {
      method: invoiceId ? "PUT" : "POST",
      body: b,
    })
    await Promise.all([mutate("/ord/invoices"), mutate(ORD_NOTIFICATIONS_KEY)])
    return saved.id
  }

  const onSave = async () => {
    setBusy("save")
    setError(null)
    setNotice(null)
    try {
      const id = await save()
      if (id) {
        setNotice("Черновик сохранён")
        if (invoiceId) await reloadInvoice()
        else onClose()
      }
    } catch (err) {
      setError(describe(err))
    } finally {
      setBusy(null)
    }
  }

  const onSubmit = async () => {
    setBusy("submit")
    setError(null)
    setNotice(null)
    try {
      const id = await save()
      if (!id) return
      const res = await api<{ status: string; error?: string | null }>(`/ord/invoices/${id}/submit`, { method: "POST" })
      await Promise.all([mutate("/ord/invoices"), mutate(ORD_NOTIFICATIONS_KEY)])
      if (res.status === "ok") setNotice("Акт принят ОРД: все строки статистики зарегистрированы")
      else if (res.status === "partial") setError("Акт отправлен, но часть строк отклонена — см. статус по строкам, исправьте и отправьте повторно")
      else setError(res.error || "ОРД отклонил акт")
      if (invoiceId) await reloadInvoice()
      else onClose()
    } catch (err) {
      await Promise.all([mutate("/ord/invoices"), mutate(ORD_NOTIFICATIONS_KEY)])
      if (invoiceId) await reloadInvoice()
      setError(describe(err))
    } finally {
      setBusy(null)
    }
  }

  const onDelete = async () => {
    if (!invoiceId || !confirm("Удалить черновик акта?")) return
    setBusy("delete")
    setError(null)
    try {
      await api(`/ord/invoices/${invoiceId}`, { method: "DELETE" })
      await Promise.all([mutate("/ord/invoices"), mutate(ORD_NOTIFICATIONS_KEY)])
      onClose()
    } catch (err) {
      setError(describe(err))
    } finally {
      setBusy(null)
    }
  }

  const title = invoiceId ? `Акт ${invoice?.number ?? ""}`.trim() : "Новый акт"
  const readOnly = !canWrite || locked

  return (
    <Modal open onClose={onClose} title={title} subtitle={`${contract.client_name} → ${contract.contractor_name} · договор ${contract.number}`} size="xl">
      {!form ? (
        previewError ? (
          <p className="text-sm text-danger">{previewError instanceof Error ? previewError.message : "Не удалось загрузить предпросмотр"}</p>
        ) : (
          <Spinner />
        )
      ) : (
        <div className="space-y-5">
          {invoice ? (
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <StatusBadge status={invoice.status} />
              {invoice.ord_external_id ? <span className="font-mono text-xs text-muted">ОРД: {invoice.ord_external_id}</span> : null}
              {invoice.submitted_at ? <span className="text-xs text-muted">отправлен {String(invoice.submitted_at).slice(0, 16).replace("T", " ")}</span> : null}
              {invoice.error ? <span className="text-xs text-danger">{invoice.error}</span> : null}
            </div>
          ) : null}

          {error ? <div role="alert" className="rounded-2xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">{error}</div> : null}
          {notice ? <div role="status" className="rounded-2xl border border-success/30 bg-success/5 px-4 py-3 text-sm text-foreground">{notice}</div> : null}

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Field label="Номер акта">
              <input required className={inputCls} value={form.number} disabled={readOnly} onChange={(e) => setForm({ ...form, number: e.target.value })} placeholder="А-2026/09-01" />
            </Field>
            <Field label="Дата акта">
              <input type="date" className={inputCls} value={form.invoice_date} disabled={readOnly} onChange={(e) => setForm({ ...form, invoice_date: e.target.value })} />
            </Field>
            <Field label="Период с">
              <input type="date" className={inputCls} value={form.period_start} disabled={readOnly || !!invoiceId} onChange={(e) => setForm({ ...form, period_start: e.target.value, splitManual: false })} />
            </Field>
            <Field label="Период по">
              <input type="date" className={inputCls} value={form.period_end} disabled={readOnly || !!invoiceId} onChange={(e) => setForm({ ...form, period_end: e.target.value, splitManual: false })} />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Field label="Сумма акта с НДС, ₽" hint={statsGross > 0 ? `по статистике ${rub(statsGross)}` : undefined}>
              <input type="number" step="0.01" min="0" className={inputCls} value={form.amount_gross} disabled={readOnly} onChange={(e) => setForm({ ...form, amount_gross: e.target.value })} />
            </Field>
            <Field label="Ставка НДС">
              <select className={inputCls} value={form.vat_rate} disabled={readOnly} onChange={(e) => setForm({ ...form, vat_rate: Number(e.target.value) })}>
                {VAT_OPTIONS.map((v) => (
                  <option key={v} value={v}>{v === 0 ? "Без НДС" : `${v} %`}</option>
                ))}
              </select>
            </Field>
            <Field label="Сумма без НДС">
              <div className="py-2 text-sm text-foreground">{rub(net)}</div>
            </Field>
            <Field label="Расхождение с фактом">
              <div className={`py-2 text-sm ${diffPct > 1 ? "text-danger" : "text-muted"}`}>
                {statsGross > 0 ? `${diffPct.toFixed(2)} %` : "—"}
                {diffPct > 1 ? <span className="block text-xs">больше 1 % — проверьте сумму (в ОРД уйдёт сумма акта)</span> : null}
              </div>
            </Field>
          </div>

          {form.amount_gross !== "" && preview?.spend_multiplier && preview.spend_multiplier !== 1 ? (
            <p className="text-xs text-muted">Расход строк показан в цене клиента (наценка ×{preview.spend_multiplier}).</p>
          ) : null}

          <section className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-medium text-foreground">Разбивка по изначальным договорам</h3>
              {!readOnly && splitRows.length > 1 ? (
                <label className="flex items-center gap-2 text-xs text-muted">
                  <input
                    type="checkbox"
                    checked={form.splitManual}
                    onChange={(e) => {
                      const split: Record<string, string> = {}
                      for (const r of splitRows) split[r.id] = r.auto
                      setForm({ ...form, splitManual: e.target.checked, split })
                    }}
                  />
                  задать суммы вручную
                </label>
              ) : null}
            </div>
            {splitRows.length === 0 ? (
              <p className="text-xs text-muted">Нет строк — нет и разбивки.</p>
            ) : (
              <Table headers={["Изначальный договор", "Заказчик", "Статус ОРД", "По статистике", "Сумма с НДС", "Без НДС"]}>
                {splitRows.map((r) => (
                  <tr key={r.id}>
                    <td className="px-4 py-2 font-medium text-foreground">{r.number}</td>
                    <td className="px-4 py-2 text-muted">{r.client?.name ?? "—"}</td>
                    <td className="px-4 py-2">
                      {r.ord_status ? <StatusBadge status={r.ord_status} /> : "—"}
                      {r.ord_status && r.ord_status !== "registered" ? <span className="ml-2 text-xs text-danger">зарегистрируйте до отправки</span> : null}
                    </td>
                    <td className="px-4 py-2 text-muted">{rub(r.fact)}</td>
                    <td className="px-4 py-2">
                      {form.splitManual && !readOnly ? (
                        <input
                          type="number"
                          step="0.01"
                          min="0"
                          className={`${inputCls} w-32`}
                          value={form.split[r.id] ?? r.auto}
                          onChange={(e) => setForm({ ...form, split: { ...form.split, [r.id]: e.target.value } })}
                          aria-label={`Сумма по договору ${r.number}`}
                        />
                      ) : (
                        <span className="text-foreground">{rub(r.value)}</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-muted">{rub(num(r.value) / (1 + form.vat_rate / 100))}</td>
                  </tr>
                ))}
              </Table>
            )}
            {splitMismatch ? (
              <p className="text-xs text-danger">Сумма разбивки {rub(splitSum)} не сходится с суммой акта {rub(form.amount_gross)}.</p>
            ) : null}
          </section>

          <section className="space-y-2">
            <h3 className="text-sm font-medium text-foreground">Строки акта — креатив × версия erid × месяц</h3>
            {items.length === 0 ? (
              <p className="text-xs text-muted">За период по договору нет показов с erid (или все уже включены в другой акт).</p>
            ) : (
              <div className="max-h-80 overflow-auto rounded-xl border border-border">
                <Table headers={["Креатив", "erid", "Месяц", "Показы", "Клики", "Расход", invoiceId ? "ОРД" : "Изнач. договор"]}>
                  {items.map((it) => (
                    <tr key={`${it.creative_id}:${it.version}:${it.period}`}>
                      <td className="px-4 py-2 text-foreground">{it.creative_name ?? it.creative_id.slice(0, 8)}</td>
                      <td className="px-4 py-2 font-mono text-xs text-muted">{it.erid ?? "—"} <span className="text-muted">v{it.version}</span></td>
                      <td className="px-4 py-2 text-muted">{it.period}</td>
                      <td className="px-4 py-2 text-muted">{it.impressions.toLocaleString("ru-RU")}</td>
                      <td className="px-4 py-2 text-muted">{it.clicks.toLocaleString("ru-RU")}</td>
                      <td className="px-4 py-2 text-foreground">{rub(it.spend)}</td>
                      <td className="px-4 py-2">
                        {invoiceId ? (
                          <>
                            <StatusBadge status={it.status ?? "pending"} />
                            {it.error ? <p className="mt-1 max-w-xs text-xs text-danger">{it.error}</p> : null}
                          </>
                        ) : (
                          <span className="text-xs text-muted">{splitRows.find((r) => r.id === String(it.contract_id))?.number ?? "—"}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </Table>
              </div>
            )}
          </section>

          {warnings.without_erid?.length ? (
            <div className="rounded-2xl border border-warning/30 bg-warning/5 px-4 py-3 text-xs text-foreground">
              Показы без erid за период (в акт не попадут, немаркированная реклама):{" "}
              {warnings.without_erid.map((w) => `${w.name} (${w.impressions.toLocaleString("ru-RU")})`).join(", ")}
            </div>
          ) : null}
          {warnings.already_invoiced?.length ? (
            <p className="text-xs text-muted">Уже в других актах: {warnings.already_invoiced.length} строк ({Array.from(new Set(warnings.already_invoiced.map((w) => w.number))).join(", ")}).</p>
          ) : null}
          {warnings.contract_not_executive ? (
            <p className="text-xs text-danger">Исполнитель договора — не наша организация: по ОРС-модели акт заключается по исполнительскому договору.</p>
          ) : null}

          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              {invoiceId && invoice && !invoice.ord_external_id && ["draft", "failed"].includes(invoice.status) && canWrite ? (
                <Button variant="secondary" disabled={busy !== null} onClick={onDelete}>{busy === "delete" ? "Удаление…" : "Удалить черновик"}</Button>
              ) : null}
            </div>
            <div className="flex gap-2">
              <Button variant="secondary" onClick={onClose}>Закрыть</Button>
              {!readOnly ? (
                <>
                  <Button variant="secondary" disabled={busy !== null || !!splitMismatch} onClick={onSave}>{busy === "save" ? "Сохранение…" : "Сохранить черновик"}</Button>
                  <Button disabled={busy !== null || !!splitMismatch} onClick={onSubmit}>
                    {busy === "submit" ? "Отправка…" : invoice?.ord_external_id || invoice?.status === "partial" ? "Отправить повторно" : "Отправить в ОРД"}
                  </Button>
                </>
              ) : null}
            </div>
          </div>
        </div>
      )}
    </Modal>
  )
}
