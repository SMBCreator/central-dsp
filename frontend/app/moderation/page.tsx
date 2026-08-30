"use client"

import { useState } from "react"
import useSWR, { mutate } from "swr"
import { api, assetUrl, fetcher } from "@/lib/api"
import { AD_TYPE_LABELS } from "@/lib/types"
import { Button, Card, CardHeader, EmptyState, Modal, Spinner, inputCls } from "@/components/ui"
import { CreativePreview } from "@/components/creative-preview"

interface QueueItem {
  id: string
  name: string
  type: "banner" | "native" | "video"
  status: string
  width: number | null
  height: number | null
  asset_url: string | null
  click_url: string | null
  native_title: string | null
  native_description: string | null
  native_cta: string | null
  native_icon_url: string | null
  video_url: string | null
  erid: string | null
  moderation_comment: string | null
  campaign_name: string
  owner_email: string
  counterparty_name: string | null
  counterparty_inn: string | null
  counterparty_ogrn: string | null
  counterparty_address: string | null
  created_at: string
}

const TABS = [
  { key: "pending", label: "На модерации" },
  { key: "approved", label: "Одобренные" },
  { key: "rejected", label: "Отклонённые" },
] as const

export default function ModerationPage() {
  const [tab, setTab] = useState<"pending" | "approved" | "rejected">("pending")
  const { data: items, isLoading } = useSWR<QueueItem[]>(`/moderation/queue?status=${tab}`, fetcher)
  const [rejecting, setRejecting] = useState<QueueItem | null>(null)
  const [comment, setComment] = useState("")
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = async () => {
    await mutate(`/moderation/queue?status=${tab}`)
    await mutate("/notifications")
  }

  const approve = async (item: QueueItem) => {
    setBusy(item.id)
    setError(null)
    try {
      await api(`/moderation/${item.id}/approve`, { method: "POST" })
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка")
    } finally {
      setBusy(null)
    }
  }

  const reject = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!rejecting) return
    setBusy(rejecting.id)
    setError(null)
    try {
      await api(`/moderation/${rejecting.id}/reject`, { method: "POST", body: { comment } })
      setRejecting(null)
      setComment("")
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка")
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">Модерация креативов</h1>
        <p className="mt-0.5 text-sm text-muted">
          Проверка рекламных материалов перед допуском к показам. Одобренные креативы уходят на регистрацию в ОРД и попадают в фид биддера.
        </p>
      </div>

      <div className="flex gap-1 rounded-md border border-border bg-surface p-0.5" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={`flex-1 rounded px-3 py-1.5 text-xs font-medium transition-colors ${
              tab === t.key ? "bg-primary text-primary-foreground" : "text-muted hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error ? <p className="text-xs text-danger">{error}</p> : null}

      {isLoading ? (
        <Spinner />
      ) : items && items.length > 0 ? (
        <div className="space-y-4">
          {items.map((item) => (
            <Card key={item.id}>
              <CardHeader
                title={item.name}
                subtitle={`${AD_TYPE_LABELS[item.type] || item.type} · Кампания «${item.campaign_name}» · Владелец: ${item.owner_email} · ${new Date(item.created_at).toLocaleString("ru-RU")}`}
              />
              <div className="flex flex-col gap-4 lg:flex-row">
                <div className="shrink-0">
                  <CreativePreview
                    type={item.type}
                    assetUrl={assetUrl(item.asset_url)}
                    width={item.width}
                    height={item.height}
                    nativeTitle={item.native_title}
                    nativeDescription={item.native_description}
                    nativeCta={item.native_cta}
                    nativeIconUrl={assetUrl(item.native_icon_url)}
                    videoUrl={assetUrl(item.video_url)}
                    advertiser={{
                      name: item.counterparty_name,
                      inn: item.counterparty_inn,
                      ogrn: item.counterparty_ogrn,
                      address: item.counterparty_address,
                      erid: item.erid,
                    }}
                  />
                </div>
                <div className="min-w-0 flex-1 space-y-2 text-sm">
                  <div className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
                    <p className="text-muted">
                      Ссылка перехода:{" "}
                      {item.click_url ? (
                        <a href={item.click_url} target="_blank" rel="noopener noreferrer" className="break-all text-primary hover:underline">
                          {item.click_url}
                        </a>
                      ) : (
                        "—"
                      )}
                    </p>
                    <p className="text-muted">
                      Рекламодатель: <span className="text-foreground">{item.counterparty_name || "не указан"}</span>
                    </p>
                    {item.counterparty_inn ? (
                      <p className="text-muted">
                        ИНН: <span className="text-foreground">{item.counterparty_inn}</span>
                      </p>
                    ) : null}
                    {item.counterparty_ogrn ? (
                      <p className="text-muted">
                        ОГРН: <span className="text-foreground">{item.counterparty_ogrn}</span>
                      </p>
                    ) : null}
                    {item.counterparty_address ? (
                      <p className="text-muted sm:col-span-2">
                        Адрес: <span className="text-foreground">{item.counterparty_address}</span>
                      </p>
                    ) : null}
                    {item.erid ? (
                      <p className="text-muted">
                        erid: <span className="font-mono text-xs text-foreground">{item.erid}</span>
                      </p>
                    ) : null}
                  </div>
                  {!item.counterparty_name && tab === "pending" ? (
                    <p className="rounded border border-warning/40 bg-warning/10 px-2 py-1.5 text-xs text-warning">
                      К креативу не привязан контрагент — маркировка (erid) не сможет пройти регистрацию в ОРД.
                    </p>
                  ) : null}
                  {item.moderation_comment ? (
                    <p className="rounded border border-danger/40 bg-danger/10 px-2 py-1.5 text-xs text-danger">
                      Причина отклонения: {item.moderation_comment}
                    </p>
                  ) : null}
                  {tab === "pending" ? (
                    <div className="flex gap-2 pt-1">
                      <Button onClick={() => approve(item)} disabled={busy === item.id}>
                        {busy === item.id ? "Обработка…" : "Одобрить"}
                      </Button>
                      <Button
                        variant="secondary"
                        onClick={() => {
                          setRejecting(item)
                          setComment("")
                        }}
                        disabled={busy === item.id}
                      >
                        Отклонить
                      </Button>
                    </div>
                  ) : null}
                </div>
              </div>
            </Card>
          ))}
        </div>
      ) : (
        <EmptyState
          title={tab === "pending" ? "Очередь пуста" : tab === "approved" ? "Нет одобренных креативов" : "Нет отклонённых креативов"}
          description={tab === "pending" ? "Новые креативы клиентов появятся здесь автоматически." : ""}
        />
      )}

      <Modal open={!!rejecting} onClose={() => setRejecting(null)} title={`Отклонить «${rejecting?.name || ""}»`}>
        <form onSubmit={reject} className="space-y-4">
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-muted">Причина отклонения (увидит владелец креатива)</span>
            <textarea
              required
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={3}
              className={inputCls}
              placeholder="Например: изображение низкого качества, отсутствует возрастная маркировка…"
            />
          </label>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setRejecting(null)}>
              Отмена
            </Button>
            <Button type="submit" disabled={!!busy}>
              {busy ? "Отклонение…" : "Отклонить"}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  )
}
