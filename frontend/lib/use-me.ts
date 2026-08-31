"use client"

import useSWR from "swr"
import { api } from "@/lib/api"
import type { PermissionFlag, Role } from "@/lib/roles"

export interface Me {
  id: string
  email: string
  name: string
  role: Role
  permissions: Record<PermissionFlag, boolean>
}

const fetcher = (path: string) => api<Me>(path)

/**
 * Текущий пользователь + эффективные permissions из GET /auth/me.
 * Кэшируется SWR-ом; общий источник истины для гейтинга UI.
 * paused=true (страницы логина/шаринга) — запрос не выполняется.
 */
export function useMe(paused = false) {
  const { data, error, isLoading, mutate } = useSWR<Me>(paused ? null : "/auth/me", fetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })
  return {
    me: data ?? null,
    isLoading,
    error,
    mutate,
  }
}
