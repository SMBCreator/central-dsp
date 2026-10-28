"use client"

import { useCallback, useRef, useState } from "react"

/**
 * ТЗ п.6: защита от двойной отправки формы. Ref блокирует повторный вызов
 * синхронно (двойной клик быстрее ререндера), state — дизейблит кнопку.
 */
export function useSubmitGuard() {
  const lock = useRef(false)
  const [submitting, setSubmitting] = useState(false)
  const guard = useCallback(
    (fn: () => Promise<void>) => async () => {
      if (lock.current) return
      lock.current = true
      setSubmitting(true)
      try {
        await fn()
      } finally {
        lock.current = false
        setSubmitting(false)
      }
    },
    [],
  )
  return { submitting, guard }
}
