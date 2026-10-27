"use client"

import { useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { mutate } from "swr"
import { api } from "@/lib/api"
import { Button, Field, inputCls } from "@/components/ui"
import { BrandLogo } from "@/components/brand-logo"

export default function RegisterPage() {
  const router = useRouter()
  const [name, setName] = useState("")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await api("/auth/register", {
        method: "POST",
        body: { name, email, password },
      })
      await mutate("/auth/me", undefined, { revalidate: false })
      router.push("/")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка регистрации")
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col gap-2">
          <h1>
            <BrandLogo height={36} alt="Central ADV" />
          </h1>
          <p className="text-xs text-muted">Платформа программатик-рекламы</p>
        </div>

        <form onSubmit={submit} className="space-y-4 rounded-lg border border-border bg-card p-6">
          <h2 className="text-sm font-semibold text-foreground">Создать аккаунт</h2>
          <Field label="Имя">
            <input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              className={inputCls}
              placeholder="Иван Петров"
              autoComplete="name"
            />
          </Field>
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
          <Field label="Пароль" hint="Минимум 8 символов">
            <input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={inputCls}
              placeholder="••••••••"
              autoComplete="new-password"
            />
          </Field>
          {error ? <p className="text-xs text-danger">{error}</p> : null}
          <Button type="submit" disabled={loading} className="w-full">
            {loading ? "Создание…" : "Зарегистрироваться"}
          </Button>
          <p className="text-center text-xs text-muted">
            Уже есть аккаунт?{" "}
            <Link href="/login" className="text-accent hover:underline">
              Войти
            </Link>
          </p>
        </form>
      </div>
    </main>
  )
}
