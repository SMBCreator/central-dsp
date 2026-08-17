"use client"

import { useState } from "react"
import useSWR from "swr"
import { api, fetcher } from "@/lib/api"
import { Button, Card, CardHeader, Field, Modal, EmptyState, Spinner, inputCls } from "@/components/ui"

interface ShareLink {
  id: string
  advertiser_id: string | null
  advertiser_name: string | null
  token: string
  name: string
  brand_name: string | null
  brand_color: string | null
  expires_at: string | null
  revoked: boolean
  created_at: string
}

interface Guest {
  id: string
  email: string
  name: string
  status: string
  advertiser_id: string | null
  advertiser_name: string | null
  brand_name: string | null
  created_at: string
}

interface Advertiser {
  id: string
  name: string
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-full bg-panel px-2.5 py-0.5 text-xs font-medium text-muted">
      {children}
    </span>
  )
}

export default function AccessPage() {
  const { data: links, mutate: mutateLinks } = useSWR<ShareLink[]>("/share/links", fetcher)
  const { data: guests, mutate: mutateGuests } = useSWR<Guest[]>("/share/guests", fetcher)
  const { data: advertisers } = useSWR<Advertiser[]>("/advertisers", fetcher)

  const [linkModal, setLinkModal] = useState(false)
  const [guestModal, setGuestModal] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Share link form
  const [lName, setLName] = useState("")
  const [lAdv, setLAdv] = useState("")
  const [lBrand, setLBrand] = useState("")
  const [lColor, setLColor] = useState("#6366f1")
  const [lDays, setLDays] = useState("")

  // Guest form
  const [gEmail, setGEmail] = useState("")
  const [gPassword, setGPassword] = useState("")
  const [gName, setGName] = useState("")
  const [gAdv, setGAdv] = useState("")
  const [gBrand, setGBrand] = useState("")

  const createLink = async () => {
    setError(null)
    try {
      await api("/share/links", {
        method: "POST",
        body: JSON.stringify({
          name: lName,
          advertiser_id: lAdv || null,
          brand_name: lBrand || null,
          brand_color: lColor || null,
          expires_days: lDays ? Number(lDays) : null,
        }),
      })
      setLinkModal(false)
      setLName("")
      setLAdv("")
      setLBrand("")
      setLDays("")
      mutateLinks()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка создания ссылки")
    }
  }

  const createGuest = async () => {
    setError(null)
    try {
      await api("/share/guests", {
        method: "POST",
        body: JSON.stringify({
          email: gEmail,
          password: gPassword,
          name: gName,
          advertiser_id: gAdv || null,
          brand_name: gBrand || null,
        }),
      })
      setGuestModal(false)
      setGEmail("")
      setGPassword("")
      setGName("")
      setGAdv("")
      setGBrand("")
      mutateGuests()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка создания гостя")
    }
  }

  const copyLink = (token: string) => {
    navigator.clipboard.writeText(`${window.location.origin}/share/${token}`)
    setCopied(token)
    setTimeout(() => setCopied(null), 2000)
  }

  const revokeLink = async (id: string) => {
    await api(`/share/links/${id}`, { method: "DELETE" })
    mutateLinks()
  }

  const deleteGuest = async (id: string) => {
    await api(`/share/guests/${id}`, { method: "DELETE" })
    mutateGuests()
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-foreground">Доступ для клиентов</h1>
        <p className="mt-1 text-sm text-muted">White-label отчёты по ссылке и гостевые аккаунты для агентств</p>
      </div>

      {/* Share links */}
      <Card>
        <CardHeader
          title="Ссылки на отчёты"
          subtitle="Публичный отчёт без логина — отправьте ссылку клиенту"
          action={<Button onClick={() => setLinkModal(true)}>Создать ссылку</Button>}
        />
        <div className="px-6 pb-6">
          {!links ? (
            <Spinner />
          ) : links.length === 0 ? (
            <EmptyState title="Пока нет ссылок" description="Создайте white-label отчёт и отправьте клиенту." />
          ) : (
            <div className="flex flex-col gap-2">
              {links.map((l) => (
                <div
                  key={l.id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-panel px-4 py-3"
                >
                  <div className="flex flex-col gap-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-foreground">{l.name}</span>
                      {l.revoked && (
                        <span className="inline-flex items-center rounded-full bg-danger/10 px-2.5 py-0.5 text-xs font-medium text-danger">
                          Отозвана
                        </span>
                      )}
                      {l.brand_name && <Chip>{l.brand_name}</Chip>}
                    </div>
                    <span className="text-xs text-muted">
                      {l.advertiser_name ? `Рекламодатель: ${l.advertiser_name}` : "Все кампании"}
                      {l.expires_at
                        ? ` · до ${new Date(l.expires_at).toLocaleDateString("ru-RU")}`
                        : " · бессрочная"}
                    </span>
                  </div>
                  {!l.revoked && (
                    <div className="flex items-center gap-2">
                      <Button variant="secondary" onClick={() => copyLink(l.token)}>
                        {copied === l.token ? "Скопировано" : "Копировать ссылку"}
                      </Button>
                      <Button variant="ghost" onClick={() => revokeLink(l.id)}>
                        Отозвать
                      </Button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>

      {/* Guest accounts */}
      <Card>
        <CardHeader
          title="Гостевые аккаунты"
          subtitle="Логин и пароль для клиента — он видит только статистику"
          action={<Button onClick={() => setGuestModal(true)}>Добавить гостя</Button>}
        />
        <div className="px-6 pb-6">
          {!guests ? (
            <Spinner />
          ) : guests.length === 0 ? (
            <EmptyState
              title="Нет гостевых аккаунтов"
              description="Создайте логин и пароль для клиента — он увидит только статистику."
            />
          ) : (
            <div className="flex flex-col gap-2">
              {guests.map((g) => (
                <div
                  key={g.id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-panel px-4 py-3"
                >
                  <div className="flex flex-col gap-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-foreground">{g.name}</span>
                      <Chip>{g.email}</Chip>
                      {g.brand_name && <Chip>{g.brand_name}</Chip>}
                    </div>
                    <span className="text-xs text-muted">
                      {g.advertiser_name ? `Рекламодатель: ${g.advertiser_name}` : "Все кампании"}
                    </span>
                  </div>
                  <Button variant="ghost" onClick={() => deleteGuest(g.id)}>
                    Удалить
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>

      {/* Create share link modal */}
      <Modal open={linkModal} onClose={() => setLinkModal(false)} title="Новая ссылка на отчёт">
        <div className="flex flex-col gap-4">
          {error && <p className="text-sm text-danger">{error}</p>}
          <Field label="Название отчёта">
            <input
              className={inputCls}
              value={lName}
              onChange={(e) => setLName(e.target.value)}
              placeholder="Отчёт для клиента X"
            />
          </Field>
          <Field label="Рекламодатель (пусто = все)">
            <select className={inputCls} value={lAdv} onChange={(e) => setLAdv(e.target.value)}>
              <option value="">Все кампании</option>
              {advertisers?.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Бренд (white label)">
            <input
              className={inputCls}
              value={lBrand}
              onChange={(e) => setLBrand(e.target.value)}
              placeholder="Agency Name"
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Цвет бренда">
              <input
                type="color"
                className="h-10 w-full cursor-pointer rounded-xl border border-border bg-panel"
                value={lColor}
                onChange={(e) => setLColor(e.target.value)}
              />
            </Field>
            <Field label="Срок (дней)" hint="Пусто = бессрочно">
              <input
                type="number"
                min={1}
                max={365}
                className={inputCls}
                value={lDays}
                onChange={(e) => setLDays(e.target.value)}
                placeholder="30"
              />
            </Field>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setLinkModal(false)}>
              Отмена
            </Button>
            <Button onClick={createLink} disabled={!lName.trim()}>
              Создать
            </Button>
          </div>
        </div>
      </Modal>

      {/* Create guest modal */}
      <Modal open={guestModal} onClose={() => setGuestModal(false)} title="Новый гостевой аккаунт">
        <div className="flex flex-col gap-4">
          {error && <p className="text-sm text-danger">{error}</p>}
          <Field label="Имя клиента">
            <input
              className={inputCls}
              value={gName}
              onChange={(e) => setGName(e.target.value)}
              placeholder="Иван Петров"
            />
          </Field>
          <Field label="Email (логин)">
            <input
              type="email"
              className={inputCls}
              value={gEmail}
              onChange={(e) => setGEmail(e.target.value)}
              placeholder="client@agency.com"
            />
          </Field>
          <Field label="Пароль" hint="Минимум 8 символов">
            <input
              type="text"
              className={`${inputCls} font-mono`}
              value={gPassword}
              onChange={(e) => setGPassword(e.target.value)}
              placeholder="Надёжный пароль"
            />
          </Field>
          <Field label="Рекламодатель (пусто = все)">
            <select className={inputCls} value={gAdv} onChange={(e) => setGAdv(e.target.value)}>
              <option value="">Все кампании</option>
              {advertisers?.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Бренд (white label)">
            <input
              className={inputCls}
              value={gBrand}
              onChange={(e) => setGBrand(e.target.value)}
              placeholder="Agency Name"
            />
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setGuestModal(false)}>
              Отмена
            </Button>
            <Button onClick={createGuest} disabled={!gName.trim() || !gEmail.trim() || gPassword.length < 8}>
              Создать
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
