/**
 * F-01: фокус-ловушка для dialog/drawer. Чистая часть (nextTabTarget) — без DOM,
 * покрыта тестом; хук useFocusTrap — тонкая DOM-обёртка. Используется Modal (ui.tsx)
 * и мобильным меню (mobile-drawer.tsx), чтобы поведение Tab/Shift+Tab/Escape/возврата
 * фокуса было одно на всё приложение.
 */
import { useEffect, useRef, type RefObject } from "react"

export const FOCUSABLE_SELECTOR =
  'input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'

/**
 * Куда перевести фокус по Tab внутри ловушки. null — браузерное поведение по
 * умолчанию (фокус остаётся внутри). Сравнение по индексу, не по ссылке:
 * вызывающий передаёт индекс активного элемента (-1 = фокус вне списка).
 */
export function nextTabTarget(count: number, activeIndex: number, shiftKey: boolean): number | null {
  if (count <= 0) return null
  // Фокус вне ловушки (например, на самом dialog с tabindex=-1): первый/последний
  if (activeIndex < 0) return shiftKey ? count - 1 : 0
  if (shiftKey && activeIndex === 0) return count - 1
  if (!shiftKey && activeIndex === count - 1) return 0
  return null
}

export function focusableWithin(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
}

export interface FocusTrapOptions {
  /** Первый элемент, получающий фокус при открытии (по умолчанию — первый focusable, не помеченный data-focus-skip) */
  initialFocus?: () => HTMLElement | null
  /** Блокировать прокрутку body пока открыт (по умолчанию да) */
  lockScroll?: boolean
}

/**
 * Пока active: Escape -> onClose; Tab/Shift+Tab циклится внутри containerRef;
 * при открытии фокус уходит внутрь, при закрытии возвращается на элемент,
 * который был активен до открытия (кнопка-инициатор).
 */
export function useFocusTrap(
  containerRef: RefObject<HTMLElement | null>,
  active: boolean,
  onClose: () => void,
  options: FocusTrapOptions = {},
) {
  const { initialFocus, lockScroll = true } = options
  // Родители обычно передают инлайн-колбэки (onClose={() => setOpen(false)}),
  // новые на каждый рендер. Если держать их в зависимостях эффекта, ввод символа
  // перезапускал эффект: фокус уходил на инициатор и обратно в первое поле.
  // Храним актуальные колбэки в ref — эффект живёт строго от открытия до закрытия.
  const onCloseRef = useRef(onClose)
  const initialFocusRef = useRef(initialFocus)
  useEffect(() => {
    onCloseRef.current = onClose
    initialFocusRef.current = initialFocus
  })
  useEffect(() => {
    if (!active) return
    const container = containerRef.current
    if (!container) return
    const opener = document.activeElement as HTMLElement | null

    const first =
      initialFocusRef.current?.() ??
      focusableWithin(container).find((el) => !el.hasAttribute("data-focus-skip")) ??
      null
    ;(first ?? container).focus()

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation()
        onCloseRef.current()
        return
      }
      if (e.key !== "Tab") return
      const nodes = focusableWithin(container)
      const target = nextTabTarget(nodes.length, nodes.indexOf(document.activeElement as HTMLElement), e.shiftKey)
      if (target === null) return
      e.preventDefault()
      nodes[target].focus()
    }
    document.addEventListener("keydown", onKey)
    const prevOverflow = document.body.style.overflow
    if (lockScroll) document.body.style.overflow = "hidden"
    return () => {
      document.removeEventListener("keydown", onKey)
      if (lockScroll) document.body.style.overflow = prevOverflow
      opener?.focus?.()
    }
  }, [active, containerRef, lockScroll])
}
