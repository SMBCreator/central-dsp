"use client"

import { useState } from "react"
import useSWR from "swr"
import { api, describeApiError, fetcher } from "@/lib/api"
import { Button, Card, CardHeader, EmptyState, Field, inputCls, Modal, Spinner, Table } from "@/components/ui"
import { toastError, toastSuccess } from "@/components/toaster"
import {
  PAGE_SIZE,
  canApplyImport,
  codesError,
  importSummary,
  parseCodesInput,
  sourceLabel,
  type DomainCategory,
  type DomainCategoryPage,
  type ImportPreview,
} from "@/lib/domain-categories"

type SourceFilter = "" | "manual" | "learned"

function listKey(q: string, source: SourceFilter, offset: number): string {
  const p = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) })
  if (q) p.set("q", q)
  if (source) p.set("source", source)
  return `/domain-categories?${p.toString()}`
}

function formatDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }) : "—"
}

export function DomainCategoriesPanel() {
  const [draftQ, setDraftQ] = useState("")
  const [q, setQ] = useState("")
  const [source, setSource] = useState<SourceFilter>("")
  const [offset, setOffset] = useState(0)
  const [editing, setEditing] = useState<DomainCategory | "new" | null>(null)
  const [deleting, setDeleting] = useState<DomainCategory | null>(null)
  const [importOpen, setImportOpen] = useState(false)

  const { data, error, isLoading, mutate } = useSWR<DomainCategoryPage>(listKey(q, source, offset), fetcher, {
    keepPreviousData: true,
  })

  const total = data?.total ?? 0
  const from = total === 0 ? 0 : offset + 1
  const to = Math.min(offset + PAGE_SIZE, total)

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader
          title="Справочник доменов"
          subtitle="Тематика IAB по домену — для запросов, в которых SSP не прислала категории. Ручные записи обучением не перезаписываются."
          action={
            <div className="flex gap-2">
              <Button variant="secondary" onClick={() => setImportOpen(true)}>
                Импорт CSV
              </Button>
              <Button onClick={() => setEditing("new")}>Добавить домен</Button>
            </div>
          }
        />
        <form
          className="flex flex-wrap items-end gap-3 px-6 pb-4 pt-2"
          onSubmit={(e) => {
            e.preventDefault()
            setOffset(0)
            setQ(draftQ.trim())
          }}
        >
          <div className="w-full sm:w-72">
            <Field label="Поиск по домену">
              <input
                className={inputCls}
                value={draftQ}
                onChange={(e) => setDraftQ(e.target.value)}
                placeholder="например, news"
                maxLength={100}
              />
            </Field>
          </div>
          <div className="w-full sm:w-48">
            <Field label="Источник">
              <select
                className={inputCls}
                value={source}
                onChange={(e) => {
                  setOffset(0)
                  setSource(e.target.value as SourceFilter)
                }}
              >
                <option value="">Все</option>
                <option value="manual">Вручную</option>
                <option value="learned">Из трафика</option>
              </select>
            </Field>
          </div>
          <Button type="submit" variant="secondary">
            Найти
          </Button>
        </form>

        {isLoading && !data ? (
          <Spinner />
        ) : error ? (
          <EmptyState
            title="Не удалось загрузить справочник"
            description={describeApiError(error)}
            action={
              <Button variant="secondary" onClick={() => mutate()}>
                Повторить
              </Button>
            }
          />
        ) : !data || data.items.length === 0 ? (
          <EmptyState
            title={q || source ? "Ничего не найдено" : "Справочник пуст"}
            description={
              q || source
                ? "Измените запрос или фильтр"
                : "Записи появятся из трафика (когда SSP присылает категории) или после импорта CSV"
            }
          />
        ) : (
          <>
            <Table headers={["Домен", "Тематика IAB", "Источник", "Наблюдений", "Обновлено", ""]}>
              {data.items.map((row) => (
                <tr key={row.domain}>
                  <td className="px-4 py-2.5 font-mono text-xs text-foreground">{row.domain}</td>
                  <td className="px-4 py-2.5 font-mono text-xs text-foreground">{row.iab_codes.join(", ")}</td>
                  <td className="px-4 py-2.5 text-xs text-muted">{sourceLabel(row.source)}</td>
                  <td className="px-4 py-2.5 text-xs tabular-nums text-muted">{row.source === "manual" ? "—" : row.hits}</td>
                  <td className="whitespace-nowrap px-4 py-2.5 text-xs text-muted">{formatDate(row.updated_at)}</td>
                  <td className="px-4 py-2.5">
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" onClick={() => setEditing(row)}>
                        Изменить
                      </Button>
                      <Button variant="danger" onClick={() => setDeleting(row)}>
                        Удалить
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </Table>
            <div className="flex items-center justify-between gap-3 px-6 pb-5 pt-1 text-xs text-muted">
              <span>
                {from}–{to} из {total}
              </span>
              <div className="flex gap-2">
                <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                  Назад
                </Button>
                <Button variant="secondary" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>
                  Вперёд
                </Button>
              </div>
            </div>
          </>
        )}
      </Card>

      <EditModal
        target={editing}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null)
          mutate()
        }}
      />
      <DeleteModal
        target={deleting}
        onClose={() => setDeleting(null)}
        onDeleted={() => {
          setDeleting(null)
          mutate()
        }}
      />
      <ImportModal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onApplied={() => {
          setImportOpen(false)
          setOffset(0)
          mutate()
        }}
      />
    </div>
  )
}

function EditModal({
  target,
  onClose,
  onSaved,
}: {
  target: DomainCategory | "new" | null
  onClose: () => void
  onSaved: () => void
}) {
  return (
    <Modal
      open={target !== null}
      onClose={onClose}
      title={target === "new" ? "Новый домен" : "Изменить тематику"}
      subtitle="Любая правка делает запись ручной: обучение её больше не меняет"
    >
      {target !== null ? <EditForm key={target === "new" ? "new" : target.domain} target={target} onClose={onClose} onSaved={onSaved} /> : null}
    </Modal>
  )
}

function EditForm({
  target,
  onClose,
  onSaved,
}: {
  target: DomainCategory | "new"
  onClose: () => void
  onSaved: () => void
}) {
  const isNew = target === "new"
  const [domain, setDomain] = useState(isNew ? "" : target.domain)
  const [codes, setCodes] = useState(isNew ? "" : target.iab_codes.join(", "))
  const [touched, setTouched] = useState(false)
  const [saving, setSaving] = useState(false)

  const err = codesError(codes)
  const domainMissing = domain.trim().length < 3

  async function save() {
    setTouched(true)
    if (err || domainMissing) return
    setSaving(true)
    try {
      await api("/domain-categories", {
        method: "PUT",
        body: { domain: domain.trim(), iab_codes: parseCodesInput(codes).codes },
      })
      toastSuccess("Сохранено")
      onSaved()
    } catch (e) {
      toastError(describeApiError(e, "Не удалось сохранить"))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault()
        void save()
      }}
    >
      <Field label="Домен" hint="Без www и схемы: news.example.ru">
        <input
          className={inputCls}
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          readOnly={!isNew}
          aria-readonly={!isNew}
          maxLength={253}
          autoComplete="off"
        />
      </Field>
      <Field label="Коды IAB" hint="Через запятую: IAB1, IAB12-3. Не больше 10">
        <textarea
          className={`${inputCls} min-h-20 font-mono`}
          value={codes}
          onChange={(e) => setCodes(e.target.value)}
          aria-invalid={touched && !!err}
        />
      </Field>
      {touched && (err || domainMissing) ? (
        <p role="alert" className="text-xs text-danger">
          {domainMissing ? "Укажите домен" : err}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>
          Отмена
        </Button>
        <Button type="submit" disabled={saving}>
          {saving ? "Сохраняю…" : "Сохранить"}
        </Button>
      </div>
    </form>
  )
}

function DeleteModal({
  target,
  onClose,
  onDeleted,
}: {
  target: DomainCategory | null
  onClose: () => void
  onDeleted: () => void
}) {
  const [busy, setBusy] = useState(false)

  async function remove() {
    if (!target) return
    setBusy(true)
    try {
      await api(`/domain-categories/${encodeURIComponent(target.domain)}`, { method: "DELETE" })
      toastSuccess("Домен удалён из справочника")
      onDeleted()
    } catch (e) {
      toastError(describeApiError(e, "Не удалось удалить"))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open={target !== null} onClose={onClose} title="Удалить домен?" subtitle={target?.domain}>
      <p className="text-sm text-muted">
        Запись исчезнет из справочника. Если домен снова встретится в трафике с категориями от SSP, он может быть выучен заново.
      </p>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>
          Отмена
        </Button>
        <Button variant="danger" onClick={remove} disabled={busy}>
          {busy ? "Удаляю…" : "Удалить"}
        </Button>
      </div>
    </Modal>
  )
}

function ImportModal({ open, onClose, onApplied }: { open: boolean; onClose: () => void; onApplied: () => void }) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title="Импорт CSV"
      subtitle="Формат строки: домен;IAB1,IAB2. Сначала предпросмотр, затем применение целиком"
    >
      {open ? <ImportForm onClose={onClose} onApplied={onApplied} /> : null}
    </Modal>
  )
}

function ImportForm({ onClose, onApplied }: { onClose: () => void; onApplied: () => void }) {
  const [csv, setCsv] = useState("")
  const [result, setResult] = useState<ImportPreview | null>(null)
  const [busy, setBusy] = useState(false)

  async function run(apply: boolean) {
    setBusy(true)
    try {
      const res = await api<ImportPreview>("/domain-categories/import", { method: "POST", body: { csv, apply } })
      if (apply) {
        toastSuccess(importSummary(res))
        onApplied()
      } else {
        setResult(res)
      }
    } catch (e) {
      toastError(describeApiError(e, "Не удалось выполнить импорт"))
    } finally {
      setBusy(false)
    }
  }

  async function onFile(file: File | undefined) {
    if (!file) return
    setCsv(await file.text())
    setResult(null)
  }

  return (
    <div className="flex flex-col gap-4">
      <Field label="Файл CSV" hint="Первая строка «domain;iab» (заголовок) необязательна. До 5000 строк">
        <input
          type="file"
          accept=".csv,text/csv,text/plain"
          className={inputCls}
          onChange={(e) => void onFile(e.target.files?.[0])}
        />
      </Field>
      <Field label="Или вставьте текст">
        <textarea
          className={`${inputCls} min-h-32 font-mono text-xs`}
          value={csv}
          onChange={(e) => {
            setCsv(e.target.value)
            setResult(null)
          }}
          placeholder={"news.example.ru;IAB12\nauto.example.ru;IAB2,IAB2-3"}
        />
      </Field>

      {result ? (
        <div className="flex flex-col gap-3">
          <p role="status" className={`text-sm ${result.errors.length ? "text-danger" : "text-foreground"}`}>
            {importSummary(result)}
          </p>
          {result.errors.length > 0 ? (
            <div className="max-h-56 overflow-y-auto rounded-lg border border-border">
              <Table headers={["Строка", "Домен", "Ошибка"]}>
                {result.errors.slice(0, 100).map((e) => (
                  <tr key={`${e.line}-${e.error}`}>
                    <td className="px-4 py-2 text-xs tabular-nums text-muted">{e.line || "—"}</td>
                    <td className="px-4 py-2 font-mono text-xs text-foreground">{e.domain || "—"}</td>
                    <td className="px-4 py-2 text-xs text-danger">{e.error}</td>
                  </tr>
                ))}
              </Table>
              {result.errors.length > 100 ? (
                <p className="px-4 pb-3 text-xs text-muted">Показаны первые 100 из {result.errors.length}</p>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>
          Отмена
        </Button>
        <Button variant="secondary" onClick={() => void run(false)} disabled={busy || csv.trim() === ""}>
          Предпросмотр
        </Button>
        <Button onClick={() => void run(true)} disabled={busy || !canApplyImport(result)}>
          Применить
        </Button>
      </div>
    </div>
  )
}
