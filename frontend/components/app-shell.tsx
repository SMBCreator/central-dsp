"use client"

import { useEffect, useState, type ReactNode } from "react"
import { usePathname, useRouter } from "next/navigation"
import { Sidebar } from "@/components/sidebar"

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const [ready, setReady] = useState(false)
  const isAuthPage = pathname === "/login" || pathname === "/register"

  useEffect(() => {
    if (isAuthPage) {
      setReady(true)
      return
    }
    const token = window.localStorage.getItem("dsp_token")
    if (!token) {
      router.replace("/login")
      return
    }
    setReady(true)
  }, [isAuthPage, pathname, router])

  if (isAuthPage) return <>{children}</>
  if (!ready) return null

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-6xl px-6 py-6">{children}</div>
      </main>
    </div>
  )
}
