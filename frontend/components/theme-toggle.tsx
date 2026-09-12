"use client"

import { useCallback, useEffect, useState } from "react"

export function ThemeToggle({ className = "" }: { className?: string }) {
  const [dark, setDark] = useState(false)

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"))
  }, [])

  const toggle = useCallback(() => {
    const next = !document.documentElement.classList.contains("dark")
    document.documentElement.classList.toggle("dark", next)
    localStorage.setItem("theme", next ? "dark" : "light")
    setDark(next)
  }, [])

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={dark ? "Светлая тема" : "Тёмная тема"}
      className={`flex h-9 w-9 items-center justify-center rounded-full bg-panel text-muted transition-colors hover:bg-surface-hover hover:text-foreground ${className}`}
    >
      {dark ? (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.7" />
          <path
            d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4l1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
          />
        </svg>
      ) : (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path
            d="M21 12.8A9 9 0 1111.2 3 7 7 0 0021 12.8z"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinejoin="round"
          />
        </svg>
      )}
    </button>
  )
}
