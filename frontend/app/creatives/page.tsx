"use client"

import { useState } from "react"
import useSWR, { mutate } from "swr"
import { api, assetUrl, fetcher } from "@/lib/api"
import { kktuShortLabel } from "@/lib/kktu"
import { AD_TYPE_LABELS, type Campaign, type Creative } from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Spinner, StatusBadge, Table } from "@/components/ui"
import { CreativeModal } from "@/components/creative-modal"
import { canWrite, isAdminRole } from "@/lib/roles"
import { useMe } from "@/lib/use-me"

export default function CreativesPage() {
  const { data: creatives } = useSWR<Creative[]>("/creatives", fetcher)
  const { data: campaigns } = useSWR<Campaign[]>("/campaigns", fetcher)
  // analyst/viewer и client без can_manage_creatives — раздел read-only (сервер тоже вернёт 403)
  const { me } = useMe()
  const writable = !me || canWrite(me.role, "creatives", me.permissions)
  // approved/rejected выставляет только /moderation (админ): там же выдаётся erid.
  // PATCH /creatives/{id} принимает лишь pending|archived — иначе обход модерации и ОРД.
  const admin = isAdminRole(me?.role)

  const [open, setOpen] = useState(false)
  const [notice, setNotice] = useState<{ kind: "ok" | "warn"; text: string } | null>(null)

  const approve = async (c: Creative) => {
    setNotice(null)
    const res = await api<Creative & { ord?: { status: string; error: string | null } }>(
      `/moderation/${c.id}/approve`,
      { method: "POST" },
    )
    if (res.ord?.status === "ok") setNotice({ kind: "ok", text: `«${c.name}» одобрен, erid ${res.erid} присвоен.` })
    else if (res.ord?.status === "not_required") setNotice({ kind: "ok", text: `«${c.name}» одобрен. Маркировка не требуется для выбранного гео.` })
    else if (res.ord)
      setNotice({
        kind: "warn",
        text: `«${c.name}» одобрен, но без erid: ${res.ord.error || "ошибка ОРД"}. До регистрации в разделе «Маркировка (ОРД)» креатив не участвует в показах на РФ.`,
      })
    await mutate("/creatives")
  }

  const reject = async (c: Creative) => {
    setNotice(null)
    await api(`/moderation/${c.id}/reject`, { method: "POST", body: { comment: "" } })
    await mutate("/creatives")
  }

  const resubmit = async (c: Creative) => {
    setNotice(null)
    await api(`/creatives/${c.id}`, { method: "PATCH", body: { status: "pending" } })
    await mutate("/creatives")
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Креативы</h1>
          <p className="mt-0.5 text-sm text-muted">Баннеры, нативные и видеообъявления</p>
        </div>
        {writable ? (
          <Button onClick={() => setOpen(true)} disabled={!campaigns || campaigns.length === 0}>
            Загрузить креатив
          </Button>
        ) : null}
      </div>

      {notice ? (
        <p role={notice.kind === "ok" ? "status" : "alert"} className={`text-xs ${notice.kind === "ok" ? "text-success" : "text-warning"}`}>
          {notice.text}
        </p>
      ) : null}

      <Card>
        <CardHeader title="Все креативы" subtitle={creatives ? `Всего: ${creatives.length}` : undefined} />
        {creatives ? (
          creatives.length > 0 ? (
            <Table headers={["ID", "Название", "Тип", "Классификатор", "Размер", "Статус", "erid", "Кампания", ""]}>
              {creatives.map((c) => {
                const camp = campaigns?.find((x) => x.id === c.campaign_id)
                return (
                  <tr key={c.id} className="hover:bg-surface">
                    <td className="px-4 py-3 font-mono text-xs text-muted">{c.display_id}</td>
                    <td className="px-4 py-3 font-medium text-foreground">{c.name}</td>
                    <td className="px-4 py-3 text-muted">{AD_TYPE_LABELS[c.type] || c.type}</td>
                    <td className="px-4 py-3 text-muted">
                      {c.kktu_code ? (
                        <span title={kktuShortLabel(c.kktu_code)} className="inline-flex items-center gap-1.5">
                          <span className="font-mono text-xs text-foreground">{c.kktu_code}</span>
                          <span className="max-w-40 truncate text-xs">{c.kktu_label || ""}</span>
                        </span>
                      ) : (
                        <span className="text-xs text-warning">не задан</span>
                      )}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-muted">
                      {c.width && c.height ? `${c.width}×${c.height}` : c.video_duration ? `${c.video_duration} сек` : "—"}
                    </td>
                    <td className="px-4 py-3" title={c.moderation_comment || undefined}>
                      <StatusBadge status={c.status} />
                      {c.status === "rejected" && c.moderation_comment ? (
                        <p className="mt-1 max-w-48 text-[10px] leading-snug text-danger">{c.moderation_comment}</p>
                      ) : null}
                    </td>
                    <td className="px-4 py-3">
                      {c.erid ? (
                        <span className="font-mono text-xs text-foreground">{c.erid}</span>
                      ) : c.status === "approved" ? (
                        <span className="text-xs text-warning" title="Одобрен, но не зарегистрирован в ОРД — в показы на РФ не попадает">
                          нет erid
                        </span>
                      ) : (
                        <span className="text-xs text-muted">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-muted">{camp?.name || "—"}</td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-2">
                        {/* H10: открыть креатив в реальном размере в новой вкладке */}
                        {c.asset_url || c.video_url ? (
                          <a
                            href={assetUrl(c.asset_url || c.video_url || "") || "#"}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center rounded-md px-2 py-1 text-xs font-medium text-muted transition-colors hover:text-foreground"
                            title="Открыть в реальном размере в новой вкладке"
                          >
                            Открыть
                            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="ml-1">
                              <path d="M7 17L17 7M17 7H8M17 7v9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                            </svg>
                          </a>
                        ) : null}
                        {admin && c.status === "pending" ? (
                          <>
                            <Button variant="ghost" onClick={() => approve(c)}>
                              Одобрить
                            </Button>
                            <Button variant="ghost" onClick={() => reject(c)}>
                              Отклонить
                            </Button>
                          </>
                        ) : writable && c.status === "rejected" ? (
                          <Button variant="ghost" onClick={() => resubmit(c)}>
                            На модерацию
                          </Button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </Table>
          ) : (
            <EmptyState
              title="Нет креативов"
              description="Загрузите баннер, нативное или видеообъявление для показа в кампаниях."
            />
          )
        ) : (
          <Spinner />
        )}
      </Card>

      <CreativeModal
        open={open}
        onClose={() => setOpen(false)}
        campaigns={campaigns || []}
        onOrdResult={(r) => {
          if (r.status === "ok") setNotice({ kind: "ok", text: `«${r.name}» одобрен, erid ${r.erid} присвоен.` })
          else if (r.status === "not_required") setNotice({ kind: "ok", text: `«${r.name}» одобрен. Маркировка не требуется для выбранного гео.` })
          else
            setNotice({
              kind: "warn",
              text: `«${r.name}» одобрен, но без erid: ${r.error || "ошибка ОРД"}. Зарегистрируйте в разделе «Маркировка (ОРД)» — до этого креатив не участвует в показах на РФ.`,
            })
        }}
      />
    </div>
  )
}
