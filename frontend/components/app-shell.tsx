"use client"

import { useEffect, useState, type ReactNode } from "react"
import { usePathname, useRouter } from "next/navigation"
import { Sidebar } from "@/components/sidebar"
import { FadeIn } from "@/components/amicro/fade-in"

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const [ready, setReady] = useState(false)
  const isAuthPage = pathname === "/login" || pathname === "/register" || pathname.startsWith("/share")

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
    <div className="flex h-screen overflow-hidden bg-background">
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        {/* Amicro fade-in page entrance, re-triggered on route change */}
        <FadeIn key={pathname} duration={0.35} className="mx-auto max-w-6xl px-6 py-6">
          {children}
        </FadeIn>
      </main>
    </div>
  )
}
