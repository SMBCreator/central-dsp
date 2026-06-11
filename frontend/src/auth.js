/**
 * auth.js — in-memory token store + login/logout helpers
 * No localStorage (blocked in sandboxed iframes).
 */
import { reactive } from 'vue'

const state = reactive({
  token: null,
  user:  null,
})

export function useAuth() {
  async function login(email, password) {
    const body = new URLSearchParams({ username: email, password })
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.detail || `HTTP ${res.status}`)
    }
    const data = await res.json()
    state.token = data.access_token

    // fetch current user
    const me = await apiFetch('/api/auth/me')
    state.user = await me.json()
  }

  function logout() {
    state.token = null
    state.user  = null
  }

  function isLoggedIn() {
    return !!state.token
  }

  return { state, login, logout, isLoggedIn }
}

/**
 * Wrapper around fetch that auto-attaches the Bearer token.
 * Use this in all views instead of raw fetch().
 */
export async function apiFetch(path, options = {}) {
  const headers = {
    ...(options.headers || {}),
    ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
  }
  if (options.body && typeof options.body === 'object' && !(options.body instanceof URLSearchParams)) {
    headers['Content-Type'] = 'application/json'
    options = { ...options, body: JSON.stringify(options.body) }
  }
  const res = await fetch(path, { ...options, headers })
  if (res.status === 401) {
    state.token = null
    state.user  = null
    window.location.href = '/login'
  }
  return res
}
