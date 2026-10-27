"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import Link from "next/link"
import useSWR, { mutate } from "swr"
import { api, fetcher } from "@/lib/api"
import { fmtMoney, fmtNum, fmtPct, type Advertiser, type Campaign } from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Field, inputCls, Modal, Spinner, StatusBadge } from "@/components/ui"
import { ConfirmDialog } from "@/components/confirm-dialog"
import { ColumnsButton, ColumnsModal, useTableColumns } from "@/components/table-columns"
import { canWrite } from "@/lib/roles"
import { useMe } from "@/lib/use-me"
import { useAdvertisers } from "@/lib/advertisers"

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
  viewability?: number
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
    render: (_c, s) => <span className="font-mono text-xs">{s && s.bids > 0 ? fmtPct((s.wins / s.bids) * 100) : "—"}</span>,
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
  {
    key: "viewability",
    label: "Viewability",
    render: (_c, s) => (
      <span className="font-mono text-xs">{s && s.impressions > 0 && s.viewability != null ? fmtPct(s.viewability) : "—"}</span>
    ),
  },
]

const DEFAULT_COLUMNS = ["daily_budget", "spent_today", "spent_total", "impressions", "clicks", "ctr"]

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

  // H2: модалка подтверждения вместо window.confirm
  const [removingGroup, setRemovingGroup] = useState<Group | null>(null)
  const [removeBusy, setRemoveBusy] = useState(false)
  const confirmRemove = async () => {
    if (!removingGroup) return
    setRemoveBusy(true)
    try {
      await api(`/groups/${removingGroup.id}`, { method: "DELETE" })
      await mutate("/groups")
      await mutate("/campaigns")
      setRemovingGroup(null)
    } finally {
      setRemoveBusy(false)
    }
  }
  const remove = (g: Group) => setRemovingGroup(g)

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
      <ConfirmDialog
        open={removingGroup !== null}
        title="Удалить группу?"
        description={`Группа «${removingGroup?.name ?? ""}» будет удалена. Кампании останутся, но будут без группы.`}
        busy={removeBusy}
        onConfirm={confirmRemove}
        onClose={() => setRemovingGroup(null)}
      />
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
                      <Button href={`/analytics?group_id=${g.id}`} variant="ghost">Статистика</Button>
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
  const { data: campaigns, error: campaignsError } = useSWR<CampaignExt[]>("/campaigns", fetcher)
  const { data: advertisers } = useAdvertisers()
  // analyst и client без can_edit_campaigns видят раздел read-only (сервер тоже вернёт 403)
  const { me } = useMe()
  const writable = !me || canWrite(me.role, "campaigns", me.permissions)
  const { data: groups } = useSWR<Group[]>("/groups", fetcher)
  const { data: breakdown } = useSWR<BreakdownRow[]>("/stats/breakdown?hours=720&dimension=campaign", fetcher)

  const [tab, setTab] = useState<"campaigns" | "groups">("campaigns")
  const [listError, setListError] = useState<string | null>(null)
  const [groupFilter, setGroupFilter] = useState("")
  const [statusFilter, setStatusFilter] = useState("")
  const [search, setSearch] = useState("")
  const [cols, setCols] = useTableColumns(
    "dsp_campaign_columns_v2",
    ALL_COLUMNS.map((c) => c.key),
    DEFAULT_COLUMNS,
  )
  const [colsOpen, setColsOpen] = useState(false)

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
    // Поиск по названию и display_id (F2: поиск по ID в фильтрах)
    const q = search.trim().toLowerCase()
    if (q) {
      list = list.filter(
        (c) =>
          c.name.toLowerCase().includes(q) ||
          String(c.display_id ?? "").includes(q) ||
          c.id.toLowerCase().includes(q),
      )
    }
    return list
  }, [campaigns, groupFilter, statusFilter, search])

  // порядок столбцов = порядок в настройке (компоновка)
  const activeCols = cols
    .map((k) => ALL_COLUMNS.find((c) => c.key === k))
    .filter(Boolean) as typeof ALL_COLUMNS

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
        {writable ? (
          <Button href="/campaigns/new" disabled={!advertisers || advertisers.length === 0}>Создать кампанию</Button>
        ) : null}
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
              <Button href="/advertisers">К рекламодателям</Button>
            }
          />
        </Card>
      ) : null}

      {tab === "groups" ? (
        <GroupsTab groups={groups} campaigns={campaigns} />
      ) : (
        <Card>
          {/* B2: горизонтальная панель — фильтры слева в ряд, действия справа */}
          <div className="flex flex-wrap items-center gap-2 px-6 pb-2 pt-5">
            <div className="w-56">
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Поиск: название или ID"
                className={`${inputCls} h-8 py-0`}
                aria-label="Поиск кампаний по названию или ID"
              />
            </div>
            <div className="w-36">
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className={`${inputCls} h-8 py-0`}
                aria-label="Фильтр по статусу"
              >
                <option value="">Все статусы</option>
                <option value="active">Активные</option>
                <option value="paused">На паузе</option>
                <option value="draft">Черновики</option>
                <option value="archived">Архив</option>
              </select>
            </div>
            <div className="w-40">
              <select
                value={groupFilter}
                onChange={(e) => setGroupFilter(e.target.value)}
                className={`${inputCls} h-8 py-0`}
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
            </div>
            <div className="ml-auto flex items-center gap-2">
              <button
                type="button"
                onClick={() => mutate("/campaigns")}
                className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted transition-colors hover:text-foreground"
                aria-label="Обновить список"
                title="Обновить список"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path d="M21 12a9 9 0 11-2.64-6.36M21 3v6h-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
              <ColumnsButton onClick={() => setColsOpen(true)} />
            </div>
          </div>

          <ColumnsModal
            open={colsOpen}
            onClose={() => setColsOpen(false)}
            all={ALL_COLUMNS.map((c) => ({ key: c.key, label: c.label }))}
            locked={["Кампания", "Статус"]}
            value={cols}
            onSave={setCols}
          />

          {listError && (
            <p role="alert" className="mx-6 mb-3 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
              {listError}
            </p>
          )}

          {campaignsError ? (
            <EmptyState
              title="Не удалось загрузить кампании"
              description={campaignsError instanceof Error ? campaignsError.message : "Проверьте соединение и попробуйте ещё раз."}
              action={<Button onClick={() => mutate("/campaigns")}>Повторить</Button>}
            />
          ) : campaigns ? (
            visible.length > 0 ? (
              /* Метрики скроллятся горизонтально, колонка «Кампания» закреплена слева (как на Авито) */
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-left">
                      <th className="sticky left-0 z-10 min-w-[200px] bg-card px-4 py-2.5 text-xs font-medium text-muted">
                        Кампания
                      </th>
                      <th className="whitespace-nowrap px-4 py-2.5 text-xs font-medium text-muted">Статус</th>
                      {activeCols.map((c) => (
                        <th key={c.key} className="whitespace-nowrap px-4 py-2.5 text-xs font-medium text-muted">
                          {c.label}
                        </th>
                      ))}
                      <th className="px-4 py-2.5" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {visible.map((c) => (
                      <tr key={c.id} className="group">
                        <td className="sticky left-0 z-10 min-w-[200px] bg-card px-4 py-3 transition-colors group-hover:bg-surface">
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
                        <td className="whitespace-nowrap px-4 py-3 transition-colors group-hover:bg-surface">
                          <StatusBadge status={c.status} />
                        </td>
                        {activeCols.map((col) => (
                          <td key={col.key} className="whitespace-nowrap px-4 py-3 text-muted transition-colors group-hover:bg-surface">
                            {col.render(c, statsById.get(c.id), groups || [])}
                          </td>
                        ))}
                        <td className="whitespace-nowrap px-4 py-3 transition-colors group-hover:bg-surface">
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
                  <tfoot>
                    <tr className="border-t border-border">
                      <td colSpan={activeCols.length + 3} className="px-4 py-2.5 text-xs text-muted">
                        Всего: {visible.length}
                        {campaigns && visible.length !== campaigns.length ? ` (отфильтровано из ${campaigns.length})` : ""}
                      </td>
                    </tr>
                  </tfoot>
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
