"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { mutate } from "swr"
import { api } from "@/lib/api"
import { homeRoute } from "@/lib/roles"
import { Button, Field, inputCls } from "@/components/ui"

export default function LoginPage() {
  const router = useRouter()
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  // H5: при редиректе после 401 показываем причину, а не молчаливый выход
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get("reason") === "session_expired") {
      setNotice("Сессия истекла — войдите заново.")
    }
  }, [])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      // Сессия приходит HttpOnly-cookie; в теле — только профиль без токена.
      const res = await api<{ role?: string }>("/auth/login", {
        method: "POST",
        body: { email, password },
      })
      // Сбрасываем закешированный /auth/me предыдущего пользователя в этой вкладке.
      await mutate("/auth/me", undefined, { revalidate: false })
      // viewer попадает на /report, finance — на /billing, остальные — на дашборд
      router.push(homeRoute(res.role))
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка входа")
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-primary">
            <span className="font-mono text-lg font-bold text-primary-foreground">C</span>
          </div>
          <div>
            <h1 className="text-lg font-semibold text-foreground">CentralDSP</h1>
            <p className="text-xs text-muted">Платформа программатик-рекламы</p>
          </div>
        </div>

        <form onSubmit={submit} className="space-y-4 rounded-lg border border-border bg-card p-6">
          <h2 className="text-sm font-semibold text-foreground">Вход в систему</h2>
          {notice ? (
            <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning" role="status">
              {notice}
            </p>
          ) : null}
          <Field label="Email">
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={inputCls}
              placeholder="you@company.com"
              autoComplete="email"
            />
          </Field>
          <Field label="Пароль">
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={inputCls}
              placeholder="••••••••"
              autoComplete="current-password"
            />
          </Field>
          {error ? <p className="text-xs text-danger">{error}</p> : null}
          <Button type="submit" disabled={loading} className="w-full">
            {loading ? "Вход…" : "Войти"}
          </Button>
          <p className="text-center text-xs text-muted">
            Нет аккаунта?{" "}
            <Link href="/register" className="text-accent hover:underline">
              Зарегистрироваться
            </Link>
          </p>
        </form>
      </div>
    </main>
  )
}
