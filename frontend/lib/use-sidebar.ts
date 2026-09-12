"use client"

import { useCallback, useSyncExternalStore } from "react"

/**
 * Состояние десктопного сайдбара: expanded | collapsed.
 * Хранится в localStorage `dsp.sidebar`; до гидратации класс `sidebar-collapsed`
 * ставится на <html> inline-скриптом в layout.tsx, чтобы не было мерцания.
 * Мобильное состояние (hidden/off-canvas) — локальный state в AppShell.
 */
export type SidebarMode = "expanded" | "collapsed"

export const SIDEBAR_STORAGE_KEY = "dsp.sidebar"
const HTML_CLASS = "sidebar-collapsed"
const EVENT = "dsp:sidebar"

function read(): SidebarMode {
  if (typeof window === "undefined") return "expanded"
  try {
    return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "collapsed" ? "collapsed" : "expanded"
  } catch {
    return "expanded"
  }
}

function write(mode: SidebarMode) {
  try {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, mode)
  } catch {
    /* приватный режим — просто не сохраняем */
  }
  document.documentElement.classList.toggle(HTML_CLASS, mode === "collapsed")
  window.dispatchEvent(new Event(EVENT))
}

function subscribe(cb: () => void) {
  window.addEventListener(EVENT, cb)
  window.addEventListener("storage", cb)
  return () => {
    window.removeEventListener(EVENT, cb)
    window.removeEventListener("storage", cb)
  }
}

export function useSidebar() {
  const mode = useSyncExternalStore(subscribe, read, () => "expanded" as SidebarMode)
  const setMode = useCallback((m: SidebarMode) => write(m), [])
  const toggle = useCallback(() => write(read() === "collapsed" ? "expanded" : "collapsed"), [])
  return { mode, collapsed: mode === "collapsed", setMode, toggle }
}

/** Хоткей Cmd/Ctrl+B не должен срабатывать при вводе текста. */
export function isEditableTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null
  if (!el) return false
  const tag = el.tagName
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable
}
