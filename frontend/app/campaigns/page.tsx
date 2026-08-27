"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import Link from "next/link"
import useSWR, { mutate } from "swr"
import { api, fetcher } from "@/lib/api"
import { fmtMoney, fmtNum, fmtPct, type Advertiser, type Campaign } from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Field, inputCls, Modal, Spinner, StatusBadge } from "@/components/ui"

const OBJECTIVES = [
  { value: "awareness", label: "Охват" },
  { value: "traffic", label: "Трафик" },
  { value: "conversions", label: "Конверсии" },
]

type Group = { id: string; display_id?: number; name: string; campaigns_count: number; spent_total: number }
type BreakdownRow = {
  id: string
  name: string
  bids: number
  wins: number
  impressions: number
  clicks: number
  ctr: number
  spend: number
}
type CampaignExt = Campaign & { group_id?: string | null }

/* ---------- Настраиваемые столбцы ---------- */

type ColumnDef = {
  key: string
  label: string
  render: (c: CampaignExt, s: BreakdownRow | undefined, groups: Group[]) => React.ReactNode
}

const ALL_COLUMNS: ColumnDef[] = [
  { key: "objective", label: "Цель", render: (c) => OBJECTIVES.find((o) => o.value === c.objective)?.label || c.objective },
  {
    key: "group",
    label: "Группа",
    render: (c, _s, groups) => groups.find((g) => g.id === c.group_id)?.name || "—",
  },
  { key: "daily_budget", label: "Бюджет в сутки", render: (c) => <span className="font-mono text-xs">{fmtMoney(c.daily_budget)}</span> },
  { key: "total_budget", label: "Общий бюджет", render: (c) => <span className="font-mono text-xs">{fmtMoney(c.total_budget)}</span> },
  { key: "spent_today", label: "Расход сегодня", render: (c) => <span className="font-mono text-xs">{fmtMoney(c.spent_today)}</span> },
  { key: "spent_total", label: "Расход всего", render: (c) => <span className="font-mono text-xs">{fmtMoney(c.spent_total)}</span> },
  { key: "impressions", label: "Показы", render: (_c, s) => <span className="font-mono text-xs">{s ? fmtNum(s.impressions) : "—"}</span> },
  { key: "clicks", label: "Клики", render: (_c, s) => <span className="font-mono text-xs">{s ? fmtNum(s.clicks) : "—"}</span> },
  { key: "ctr", label: "CTR", render: (_c, s) => <span className="font-mono text-xs">{s && s.impressions > 0 ? fmtPct(s.ctr) : "—"}</span> },
  { key: "bids", label: "Ставки", render: (_c, s) => <span className="font-mono text-xs">{s ? fmtNum(s.bids) : "—"}</span> },
  { key: "wins", label: "Выигрыши", render: (_c, s) => <span className="font-mono text-xs">{s ? fmtNum(s.wins) : "—"}</span> },
  {
    key: "win_rate",
    label: "Win rate",
    render: (_c, s) => <span className="font-mono text-xs">{s && s.bids > 0 ? fmtPct(s.wins / s.bids) : "—"}</span>,
  },
  {
    key: "cpm",
    label: "eCPM",
    render: (_c, s) => (
      <span className="font-mono text-xs">{s && s.impressions > 0 ? fmtMoney((s.spend / s.impressions) * 1000) : "—"}</span>
    ),
  },
  {
    key: "cpc",
    label: "CPC",
    render: (_c, s) => <span className="font-mono text-xs">{s && s.clicks > 0 ? fmtMoney(s.spend / s.clicks) : "—"}</span>,
  },
]

const DEFAULT_COLUMNS = ["daily_budget", "spent_today", "spent_total", "impressions", "clicks", "ctr"]
const COLUMNS_LS_KEY = "dsp_campaign_columns_v1"

function useColumns(): [string[], (keys: string[]) => void] {
  const [cols, setCols] = useState<string[]>(DEFAULT_COLUMNS)
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(COLUMNS_LS_KEY)
      if (raw) {
        const parsed = JSON.parse(raw) as string[]
        if (Array.isArray(parsed) && parsed.length) setCols(parsed.filter((k) => ALL_COLUMNS.some((c) => c.key === k)))
      }
    } catch {
      // ignore
    }
  }, [])
  const save = (keys: string[]) => {
    setCols(keys)
    try {
      window.localStorage.setItem(COLUMNS_LS_KEY, JSON.stringify(keys))
    } catch {
      // ignore
    }
  }
  return [cols, save]
}

/* ---------- Иконки-действия строки ---------- */

function RowActions({ campaign }: { campaign: CampaignExt }) {
  return (
    <div className="flex items-center justify-end gap-1">
      <Link
        href={`/analytics?campaign_id=${campaign.id}`}
        title="Статистика кампании"
        aria-label={`Статистика: ${campaign.name}`}
        className="flex h-7 w-7 items-center justify-center rounded-md text-muted transition-colors hover:bg-panel hover:text-accent"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M4 20h16M7 16v-5m5 5V8m5 8v-3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      </Link>
      <Link
        href={`/campaigns/${campaign.id}`}
        title="Настройки кампании"
        aria-label={`Настройки: ${campaign.name}`}
        className="flex h-7 w-7 items-center justify-center rounded-md text-muted transition-colors hover:bg-panel hover:text-accent"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path
            d="M12 9a3 3 0 100 6 3 3 0 000-6zm7.5 3a7.5 7.5 0 01-.1 1.2l2 1.6-2 3.4-2.4-1a7.6 7.6 0 01-2 1.2L14.6 21H9.4l-.4-2.6a7.6 7.6 0 01-2-1.2l-2.4 1-2-3.4 2-1.6a7.7 7.7 0 010-2.4l-2-1.6 2-3.4 2.4 1a7.6 7.6 0 012-1.2L9.4 3h5.2l.4 2.6a7.6 7.6 0 012 1.2l2.4-1 2 3.4-2 1.6c.06.4.1.8.1 1.2z"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinejoin="round"
          />
        </svg>
      </Link>
    </div>
  )
}

/* ---------- Выбор столбцов ---------- */

function ColumnsPicker({
  cols,
  onChange,
}: {
  cols: string[]
  onChange: (keys: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onDoc)
    return () => document.removeEventListener("mousedown", onDoc)
  }, [open])

  const toggleKey = (key: string) => {
    onChange(cols.includes(key) ? cols.filter((k) => k !== key) : [...cols, key])
  }

  return (
    <div className="relative" ref={ref}>
      <Button variant="secondary" onClick={() => setOpen(!open)}>
        Столбцы
      </Button>
      {open ? (
        <div className="absolute right-0 z-20 mt-1 w-56 rounded-xl border border-border bg-card p-2 shadow-lg">
          <p className="px-2 pb-1.5 pt-1 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
            Столбцы таблицы
          </p>
          <div className="max-h-72 space-y-0.5 overflow-y-auto">
            {ALL_COLUMNS.map((c) => (
              <label
                key={c.key}
                className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-sm text-foreground hover:bg-panel"
              >
                <input
                  type="checkbox"
                  checked={cols.includes(c.key)}
                  onChange={() => toggleKey(c.key)}
                  className="h-3.5 w-3.5 rounded border-border accent-current"
                />
                {c.label}
              </label>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

/* ---------- Вкладка «Группы кампаний» ---------- */

function GroupsTab({
  groups,
  campaigns,
}: {
  groups: Group[] | undefined
  campaigns: CampaignExt[] | undefined
}) {
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Group | null>(null)
  const [name, setName] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [assigning, setAssigning] = useState(false)

  const openCreate = () => {
    setEditing(null)
    setName("")
    setError(null)
    setModalOpen(true)
  }
  const openRename = (g: Group) => {
    setEditing(g)
    setName(g.name)
    setError(null)
    setModalOpen(true)
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      if (editing) {
        await api(`/groups/${editing.id}`, { method: "PATCH", body: { name: name.trim() } })
      } else {
        await api("/groups", { method: "POST", body: { name: name.trim() } })
      }
      await mutate("/groups")
      setModalOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка сохранения")
    } finally {
      setSaving(false)
    }
  }

  const remove = async (g: Group) => {
    if (!window.confirm(`Удалить группу «${g.name}»? Кампании останутся, но будут без группы.`)) return
    await api(`/groups/${g.id}`, { method: "DELETE" })
    await mutate("/groups")
    await mutate("/campaigns")
  }

  const setCampaignGroup = async (campaignId: string, groupId: string | null) => {
    setAssigning(true)
    try {
      await api(`/campaigns/${campaignId}`, { method: "PATCH", body: { group_id: groupId } })
      await mutate("/campaigns")
      await mutate("/groups")
    } finally {
      setAssigning(false)
    }
  }

  return (
    <Card>
      <CardHeader
        title="Группы кампаний"
        subtitle="Объединяйте кампании в группы для фильтрации и сводной статистики"
        action={<Button onClick={openCreate}>Создать группу</Button>}
      />
      {groups ? (
        groups.length > 0 ? (
          <div className="divide-y divide-border">
            {groups.map((g) => {
              const inGroup = (campaigns || []).filter((c) => c.group_id === g.id)
              const free = (campaigns || []).filter((c) => !c.group_id)
              const isOpen = expanded === g.id
              return (
                <div key={g.id}>
                  <div className="flex flex-wrap items-center justify-between gap-2 px-6 py-3">
                    <button
                      type="button"
                      onClick={() => setExpanded(isOpen ? null : g.id)}
                      className="flex items-center gap-2.5 text-left"
                      aria-expanded={isOpen}
                    >
                      <svg
                        width="14"
                        height="14"
                        viewBox="0 0 24 24"
                        fill="none"
                        aria-hidden="true"
                        className={`text-muted transition-transform ${isOpen ? "rotate-90" : ""}`}
                      >
                        <path d="M9 6l6 6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                      <span className="text-sm font-medium text-foreground">{g.name}</span>
                      <span className="rounded-md bg-panel px-2 py-0.5 font-mono text-[11px] text-muted">
                        {g.campaigns_count} камп.
                      </span>
                      <span className="font-mono text-[11px] text-muted">расход {fmtMoney(g.spent_total)}</span>
                    </button>
                    <div className="flex items-center gap-1.5">
                      <Link href={`/analytics?group_id=${g.id}`}>
                        <Button variant="ghost">Статистика</Button>
                      </Link>
                      <Button variant="ghost" onClick={() => openRename(g)}>
                        Переименовать
                      </Button>
                      <Button variant="ghost" onClick={() => remove(g)}>
                        Удалить
                      </Button>
                    </div>
                  </div>
                  {isOpen ? (
                    <div className="space-y-2 bg-panel/40 px-6 py-3">
                      {inGroup.length > 0 ? (
                        inGroup.map((c) => (
                          <div key={c.id} className="flex flex-wrap items-center justify-between gap-2">
                            <div className="flex items-center gap-2.5">
                              <Link href={`/campaigns/${c.id}`} className="text-sm font-medium text-foreground hover:text-accent">
                                {c.name}
                              </Link>
                              <span className="font-mono text-[11px] text-muted">#{c.display_id}</span>
                              <StatusBadge status={c.status} />
                            </div>
                            <Button variant="ghost" disabled={assigning} onClick={() => setCampaignGroup(c.id, null)}>
                              Убрать из группы
                            </Button>
                          </div>
                        ))
                      ) : (
                        <p className="text-xs text-muted">В группе пока нет кампаний</p>
                      )}
                      {free.length > 0 ? (
                        <div className="border-t border-border pt-2">
                          <p className="mb-1.5 text-xs text-muted">Добавить кампанию в группу:</p>
                          <div className="flex flex-wrap gap-1.5">
                            {free.map((c) => (
                              <button
                                key={c.id}
                                type="button"
                                disabled={assigning}
                                onClick={() => setCampaignGroup(c.id, g.id)}
                                className="rounded-full bg-panel px-3 py-1 text-xs font-medium text-muted transition-colors hover:text-foreground"
                              >
                                + {c.name}
                              </button>
                            ))}
                          </div>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              )
            })}
          </div>
        ) : (
          <EmptyState
            title="Нет групп"
            description="Создайте группу, чтобы объединить кампании: например, по продукту или заказчику."
            action={<Button onClick={openCreate}>Создать группу</Button>}
          />
        )
      ) : (
        <Spinner />
      )}

      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title={editing ? "Переименовать группу" : "Новая группа"}>
        <form onSubmit={save} className="space-y-4">
          <Field label="Название группы">
            <input required value={name} onChange={(e) => setName(e.target.value)} className={inputCls} placeholder="Продукт А — лето" />
          </Field>
          {error ? <p className="text-xs text-danger">{error}</p> : null}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setModalOpen(false)}>
              Отмена
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? "Сохранение…" : editing ? "Сохранить" : "Создать"}
            </Button>
          </div>
        </form>
      </Modal>
    </Card>
  )
}

/* ---------- Страница ---------- */

export default function CampaignsPage() {
  const { data: campaigns } = useSWR<CampaignExt[]>("/campaigns", fetcher)
  const { data: advertisers } = useSWR<Advertiser[]>("/advertisers", fetcher)
  const { data: groups } = useSWR<Group[]>("/groups", fetcher)
  const { data: breakdown } = useSWR<BreakdownRow[]>("/stats/breakdown?hours=720&dimension=campaign", fetcher)

  const [tab, setTab] = useState<"campaigns" | "groups">("campaigns")
  const [listError, setListError] = useState<string | null>(null)
  const [groupFilter, setGroupFilter] = useState("")
  const [statusFilter, setStatusFilter] = useState("")
  const [cols, setCols] = useColumns()

  const statsById = useMemo(() => {
    const m = new Map<string, BreakdownRow>()
    for (const r of breakdown || []) m.set(r.id, r)
    return m
  }, [breakdown])

  const visible = useMemo(() => {
    let list = campaigns || []
    if (groupFilter === "__none__") list = list.filter((c) => !c.group_id)
    else if (groupFilter) list = list.filter((c) => c.group_id === groupFilter)
    if (statusFilter) list = list.filter((c) => c.status === statusFilter)
    return list
  }, [campaigns, groupFilter, statusFilter])

  const activeCols = ALL_COLUMNS.filter((c) => cols.includes(c.key))

  const toggleStatus = async (c: CampaignExt) => {
    setListError(null)
    try {
      await api(`/campaigns/${c.id}`, {
        method: "PATCH",
        body: { status: c.status === "active" ? "paused" : "active" },
      })
      await mutate("/campaigns")
    } catch (err) {
      setListError(err instanceof Error ? err.message : "Не удалось изменить статус")
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Кампании</h1>
          <p className="mt-0.5 text-sm text-muted">Управление рекламными кампаниями и группами</p>
        </div>
        <Link href="/campaigns/new">
          <Button disabled={!advertisers || advertisers.length === 0}>Создать кампанию</Button>
        </Link>
      </div>

      <div className="flex items-center gap-1 border-b border-border" role="tablist" aria-label="Разделы">
        {[
          { key: "campaigns" as const, label: "Кампании" },
          { key: "groups" as const, label: "Группы кампаний" },
        ].map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={`-mb-px border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
              tab === t.key
                ? "border-accent text-foreground"
                : "border-transparent text-muted hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {advertisers && advertisers.length === 0 ? (
        <Card>
          <EmptyState
            title="Сначала добавьте рекламодателя"
            description="Кампания создаётся для конкретного рекламодателя."
            action={
              <Link href="/advertisers">
                <Button>К рекламодателям</Button>
              </Link>
            }
          />
        </Card>
      ) : null}

      {tab === "groups" ? (
        <GroupsTab groups={groups} campaigns={campaigns} />
      ) : (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3 px-6 pb-2 pt-5">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold text-foreground">Все кампании</h2>
              {campaigns ? <span className="font-mono text-[11px] text-muted">{visible.length} из {campaigns.length}</span> : null}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={groupFilter}
                onChange={(e) => setGroupFilter(e.target.value)}
                className={`${inputCls} h-8 w-auto min-w-36 py-0`}
                aria-label="Фильтр по группе"
              >
                <option value="">Все группы</option>
                <option value="__none__">Без группы</option>
                {(groups || []).map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
              </select>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className={`${inputCls} h-8 w-auto min-w-32 py-0`}
                aria-label="Фильтр по статусу"
              >
                <option value="">Все статусы</option>
                <option value="active">Активные</option>
                <option value="paused">На паузе</option>
                <option value="draft">Черновики</option>
                <option value="archived">Архив</option>
              </select>
              <ColumnsPicker cols={cols} onChange={setCols} />
            </div>
          </div>

          {listError && (
            <p role="alert" className="mx-6 mb-3 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
              {listError}
            </p>
          )}

          {campaigns ? (
            visible.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-left">
                      <th className="px-4 py-2.5 text-xs font-medium text-muted">Кампания</th>
                      <th className="px-4 py-2.5 text-xs font-medium text-muted">Статус</th>
                      {activeCols.map((c) => (
                        <th key={c.key} className="px-4 py-2.5 text-xs font-medium text-muted">
                          {c.label}
                        </th>
                      ))}
                      <th className="px-4 py-2.5" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {visible.map((c) => (
                      <tr key={c.id} className="hover:bg-surface">
                        <td className="px-4 py-3">
                          <Link href={`/campaigns/${c.id}`} className="font-medium text-foreground hover:text-accent">
                            {c.name}
                          </Link>
                          <p className="mt-0.5 font-mono text-[11px] text-muted">ID: {c.display_id}</p>
                          {c.group_id ? (
                            <p className="mt-0.5 text-[11px] text-muted">
                              {groups?.find((g) => g.id === c.group_id)?.name}
                            </p>
                          ) : null}
                        </td>
                        <td className="px-4 py-3">
                          <StatusBadge status={c.status} />
                        </td>
                        {activeCols.map((col) => (
                          <td key={col.key} className="px-4 py-3 text-muted">
                            {col.render(c, statsById.get(c.id), groups || [])}
                          </td>
                        ))}
                        <td className="px-4 py-3">
                          <div className="flex items-center justify-end gap-2">
                            <Button variant="ghost" onClick={() => toggleStatus(c)}>
                              {c.status === "active" ? "Пауза" : "Запустить"}
                            </Button>
                            <RowActions campaign={c} />
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState title="Нет кампаний" description="Создайте первую кампанию для запуска рекламы." />
            )
          ) : (
            <Spinner />
          )}
        </Card>
      )}
    </div>
  )
}
