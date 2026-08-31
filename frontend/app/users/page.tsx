"use client"

import { useMemo, useState } from "react"
import useSWR from "swr"
import { api, fetcher } from "@/lib/api"
import { Button, Card, CardHeader, Field, Modal, EmptyState, Spinner, inputCls } from "@/components/ui"

interface PlatformUser {
  id: string
  email: string
  name: string
  role: string
  status: string
  parent_user_id: string | null
  markup_pct: number
  effective_markup_pct: number
  permissions: Record<string, boolean>
  created_at: string
}

const ROLE_LABELS: Record<string, string> = {
  superadmin: "Суперадмин",
  admin: "Администратор",
  manager: "Менеджер (байер)",
  advertiser: "Рекламодатель",
  agency: "Агентство",
  client: "Клиент",
  analyst: "Аналитик",
  finance: "Финансы",
  viewer: "Наблюдатель (отчёт)",
}

const CREATABLE_ROLES = ["manager", "agency", "client", "advertiser", "analyst", "finance", "admin", "viewer"]

// 4 пофлаговых права роли client — зеркалят PERMISSION_FLAGS бэкенда (rbac.py)
const PERMISSION_LABELS: Record<string, string> = {
  can_edit_campaigns: "Может редактировать кампании (self-serve)",
  can_manage_creatives: "Может управлять креативами",
  can_view_billing: "Видит биллинг",
  can_share_stats: "Может делиться статистикой (ссылки и гости)",
}

function RoleBadge({ role }: { role: string }) {
  const tone =
    role === "superadmin" || role === "admin"
      ? "bg-foreground text-background"
      : role === "agency"
        ? "bg-accent text-accent-foreground"
        : "bg-panel text-muted"
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${tone}`}>
      {ROLE_LABELS[role] ?? role}
    </span>
  )
}

/** Дерево: родители сверху, дети с отступом. */
function buildTree(users: PlatformUser[]): Array<PlatformUser & { depth: number }> {
  const byParent = new Map<string | null, PlatformUser[]>()
  const ids = new Set(users.map((u) => u.id))
  for (const u of users) {
    // родитель вне видимости -> показываем как корень
    const key = u.parent_user_id && ids.has(u.parent_user_id) ? u.parent_user_id : null
    if (!byParent.has(key)) byParent.set(key, [])
    byParent.get(key)!.push(u)
  }
  const out: Array<PlatformUser & { depth: number }> = []
  const walk = (parentId: string | null, depth: number) => {
    for (const u of byParent.get(parentId) ?? []) {
      out.push({ ...u, depth })
      walk(u.id, depth + 1)
    }
  }
  walk(null, 0)
  return out
}

export default function UsersPage() {
  const { data: users, mutate, isLoading } = useSWR<PlatformUser[]>("/users", fetcher)
  const [createOpen, setCreateOpen] = useState(false)
  const [editUser, setEditUser] = useState<PlatformUser | null>(null)
  const [error, setError] = useState<string | null>(null)

  // create form
  const [cEmail, setCEmail] = useState("")
  const [cName, setCName] = useState("")
  const [cPassword, setCPassword] = useState("")
  const [cRole, setCRole] = useState("client")
  const [cParent, setCParent] = useState("")
  const [cMarkup, setCMarkup] = useState("0")
  const [cPerms, setCPerms] = useState<Record<string, boolean>>({})

  // edit form
  const [eMarkup, setEMarkup] = useState("0")
  const [ePerms, setEPerms] = useState<Record<string, boolean>>({})
  const [eStatus, setEStatus] = useState("active")

  const tree = useMemo(() => buildTree(users ?? []), [users])
  const parents = useMemo(
    () => (users ?? []).filter((u) => ["agency", "manager", "superadmin", "admin"].includes(u.role)),
    [users],
  )

  const openCreate = () => {
    setCEmail(""); setCName(""); setCPassword(""); setCRole("client")
    setCParent(""); setCMarkup("0"); setCPerms({}); setError(null)
    setCreateOpen(true)
  }

  const openEdit = (u: PlatformUser) => {
    setEMarkup(String(u.markup_pct)); setEPerms(u.permissions ?? {})
    setEStatus(u.status); setError(null)
    setEditUser(u)
  }

  const createUser = async () => {
    setError(null)
    try {
      await api("/users", {
        method: "POST",
        body: JSON.stringify({
          email: cEmail, name: cName, password: cPassword, role: cRole,
          parent_user_id: cParent || null,
          markup_pct: Number(cMarkup) || 0,
          permissions: cPerms,
        }),
      })
      setCreateOpen(false)
      mutate()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка создания")
    }
  }

  const saveEdit = async () => {
    if (!editUser) return
    setError(null)
    try {
      await api(`/users/${editUser.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          markup_pct: Number(eMarkup) || 0,
          permissions: ePerms,
          status: eStatus,
        }),
      })
      setEditUser(null)
      mutate()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка сохранения")
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Пользователи и доступы</h1>
          <p className="mt-1 text-sm text-muted">
            Иерархия: платформа → менеджеры и агентства → клиенты. Наценка применяется по цепочке.
          </p>
        </div>
        <Button onClick={openCreate}>Добавить пользователя</Button>
      </div>

      <Card>
        <CardHeader title="Структура доступов" subtitle="Наценка на трафик и права редактирования — на каждом уровне" />
        {isLoading ? (
          <div className="flex justify-center py-10"><Spinner /></div>
        ) : tree.length === 0 ? (
          <EmptyState title="Нет пользователей" description="Добавь менеджера, агентство или клиента" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                  <th className="px-3 py-2 font-medium">Пользователь</th>
                  <th className="px-3 py-2 font-medium">Роль</th>
                  <th className="px-3 py-2 font-medium">Наценка</th>
                  <th className="px-3 py-2 font-medium">Итоговая наценка</th>
                  <th className="px-3 py-2 font-medium">Права</th>
                  <th className="px-3 py-2 font-medium">Статус</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {tree.map((u) => (
                  <tr key={u.id} className="border-b border-border/60 last:border-0">
                    <td className="px-3 py-2.5">
                      <div className="flex items-center gap-2" style={{ paddingLeft: `${u.depth * 20}px` }}>
                        {u.depth > 0 && <span className="text-muted">└</span>}
                        <div>
                          <div className="font-medium">{u.name}</div>
                          <div className="text-xs text-muted">{u.email}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-2.5"><RoleBadge role={u.role} /></td>
                    <td className="px-3 py-2.5 tabular-nums">{u.markup_pct > 0 ? `+${u.markup_pct}%` : "—"}</td>
                    <td className="px-3 py-2.5 tabular-nums text-muted">
                      {u.effective_markup_pct > 0 ? `+${u.effective_markup_pct.toFixed(1)}%` : "—"}
                    </td>
                    <td className="px-3 py-2.5">
                      {u.role === "client" ? (
                        (() => {
                          const active = Object.keys(PERMISSION_LABELS).filter((k) => u.permissions?.[k])
                          return active.length > 0 ? (
                            <span className="text-xs">
                              {active.length === Object.keys(PERMISSION_LABELS).length
                                ? "Все права self-serve"
                                : `Права: ${active.length} из ${Object.keys(PERMISSION_LABELS).length}`}
                            </span>
                          ) : (
                            <span className="text-xs text-muted">Только статистика</span>
                          )
                        })()
                      ) : u.role === "viewer" ? (
                        <span className="text-xs text-muted">Только страница отчёта</span>
                      ) : (
                        <span className="text-xs text-muted">Полные для роли</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5">
                      <span className={u.status === "active" ? "text-xs" : "text-xs text-destructive"}>
                        {u.status === "active" ? "Активен" : "Заблокирован"}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      {u.role !== "superadmin" && (
                        <Button variant="ghost" onClick={() => openEdit(u)}>Настроить</Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="Новый пользователь">
        <div className="flex flex-col gap-4">
          <Field label="Роль">
            <select className={inputCls} value={cRole} onChange={(e) => setCRole(e.target.value)}>
              {CREATABLE_ROLES.map((r) => (
                <option key={r} value={r}>{ROLE_LABELS[r]}</option>
              ))}
            </select>
          </Field>
          <Field label="Имя / название">
            <input className={inputCls} value={cName} onChange={(e) => setCName(e.target.value)} placeholder="ООО Агентство или Иван Петров" />
          </Field>
          <Field label="Email">
            <input className={inputCls} type="email" value={cEmail} onChange={(e) => setCEmail(e.target.value)} />
          </Field>
          <Field label="Пароль (мин. 8 символов)">
            <input className={inputCls} type="password" value={cPassword} onChange={(e) => setCPassword(e.target.value)} />
          </Field>
          {cRole === "client" && (
            <Field label="Родитель (агентство / менеджер)">
              <select className={inputCls} value={cParent} onChange={(e) => setCParent(e.target.value)}>
                <option value="">Платформа (без агентства)</option>
                {parents.map((p) => (
                  <option key={p.id} value={p.id}>{p.name} ({ROLE_LABELS[p.role]})</option>
                ))}
              </select>
            </Field>
          )}
          <Field label="Наценка на трафик, %">
            <input className={inputCls} type="number" min="0" max="500" step="0.5" value={cMarkup} onChange={(e) => setCMarkup(e.target.value)} />
          </Field>
          {cRole === "client" && (
            <div className="flex flex-col gap-2">
              {Object.entries(PERMISSION_LABELS).map(([key, label]) => (
                <label key={key} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={!!cPerms[key]}
                    onChange={(e) => setCPerms({ ...cPerms, [key]: e.target.checked })}
                  />
                  {label}
                </label>
              ))}
              <p className="text-xs text-muted">Без флагов клиент видит только статистику.</p>
            </div>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>Отмена</Button>
            <Button onClick={createUser} disabled={!cEmail || !cName || cPassword.length < 8}>Создать</Button>
          </div>
        </div>
      </Modal>

      <Modal open={!!editUser} onClose={() => setEditUser(null)} title={editUser ? `Настройки: ${editUser.name}` : ""}>
        <div className="flex flex-col gap-4">
          <Field label="Наценка на трафик, %">
            <input className={inputCls} type="number" min="0" max="500" step="0.5" value={eMarkup} onChange={(e) => setEMarkup(e.target.value)} />
          </Field>
          <Field label="Статус">
            <select className={inputCls} value={eStatus} onChange={(e) => setEStatus(e.target.value)}>
              <option value="active">Активен</option>
              <option value="blocked">Заблокирован</option>
            </select>
          </Field>
          {editUser?.role === "client" && (
            <div className="flex flex-col gap-2">
              {Object.entries(PERMISSION_LABELS).map(([key, label]) => (
                <label key={key} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={!!ePerms[key]}
                    onChange={(e) => setEPerms({ ...ePerms, [key]: e.target.checked })}
                  />
                  {label}
                </label>
              ))}
            </div>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setEditUser(null)}>Отмена</Button>
            <Button onClick={saveEdit}>Сохранить</Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
