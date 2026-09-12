"use client"

// H9: профиль пользователя — смена имени и пароля

import { useState } from "react"
import useSWR, { mutate } from "swr"
import { api, fetcher } from "@/lib/api"
import { Button, Card, CardHeader, Field, inputCls, Spinner } from "@/components/ui"
import { toastSuccess } from "@/components/toaster"

type Me = { id: string; email: string; name: string; role: string }

const ROLE_LABELS: Record<string, string> = {
  superadmin: "Суперадмин",
  manager: "Менеджер",
  viewer: "Наблюдатель",
}

export default function ProfilePage() {
  const { data: me, isLoading } = useSWR<Me>("/auth/me", fetcher)

  const [name, setName] = useState<string | null>(null)
  const [savingName, setSavingName] = useState(false)

  const [pw, setPw] = useState({ current: "", next: "", confirm: "" })
  const [savingPw, setSavingPw] = useState(false)
  const [pwError, setPwError] = useState<string | null>(null)

  const saveName = async (e: React.FormEvent) => {
    e.preventDefault()
    const value = (name ?? me?.name ?? "").trim()
    if (!value || savingName) return
    setSavingName(true)
    try {
      await api("/auth/me", { method: "PATCH", body: { name: value } })
      await mutate("/auth/me")
      toastSuccess("Имя обновлено")
    } catch {
      // ошибка показана toast-обработчиком
    } finally {
      setSavingName(false)
    }
  }

  const savePassword = async (e: React.FormEvent) => {
    e.preventDefault()
    setPwError(null)
    if (pw.next.length < 8) {
      setPwError("Новый пароль должен быть не короче 8 символов.")
      return
    }
    if (pw.next !== pw.confirm) {
      setPwError("Пароли не совпадают.")
      return
    }
    if (savingPw) return
    setSavingPw(true)
    try {
      await api("/auth/change-password", {
        method: "POST",
        body: { current_password: pw.current, new_password: pw.next },
      })
      setPw({ current: "", next: "", confirm: "" })
      toastSuccess("Пароль изменён")
    } catch (err) {
      setPwError(err instanceof Error ? err.message : "Не удалось изменить пароль")
    } finally {
      setSavingPw(false)
    }
  }

  if (isLoading || !me) return <Spinner />

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">Профиль</h1>
        <p className="mt-0.5 text-sm text-muted">
          {me.email} · {ROLE_LABELS[me.role] || me.role}
        </p>
      </div>

      <Card>
        <CardHeader title="Имя" subtitle="Отображается в интерфейсе и уведомлениях" />
        <form onSubmit={saveName} className="flex items-end gap-3 px-6 pb-5">
          <div className="flex-1">
            <Field label="Имя">
              <input
                type="text"
                required
                maxLength={120}
                value={name ?? me.name}
                onChange={(e) => setName(e.target.value)}
                className={inputCls}
                autoComplete="name"
              />
            </Field>
          </div>
          <Button type="submit" disabled={savingName}>
            {savingName ? "Сохранение…" : "Сохранить"}
          </Button>
        </form>
      </Card>

      <Card>
        <CardHeader title="Смена пароля" subtitle="Минимум 8 символов" />
        <form onSubmit={savePassword} className="space-y-4 px-6 pb-5">
          <Field label="Текущий пароль">
            <input
              type="password"
              required
              value={pw.current}
              onChange={(e) => setPw({ ...pw, current: e.target.value })}
              className={inputCls}
              autoComplete="current-password"
            />
          </Field>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Новый пароль">
              <input
                type="password"
                required
                minLength={8}
                value={pw.next}
                onChange={(e) => setPw({ ...pw, next: e.target.value })}
                className={inputCls}
                autoComplete="new-password"
              />
            </Field>
            <Field label="Повторите новый пароль">
              <input
                type="password"
                required
                value={pw.confirm}
                onChange={(e) => setPw({ ...pw, confirm: e.target.value })}
                className={inputCls}
                autoComplete="new-password"
              />
            </Field>
          </div>
          {pwError ? <p className="text-xs text-danger">{pwError}</p> : null}
          <Button type="submit" disabled={savingPw}>
            {savingPw ? "Сохранение…" : "Изменить пароль"}
          </Button>
        </form>
      </Card>
    </div>
  )
}
