<template>
  <div class="login-wrap">
    <div class="login-box">
      <!-- Logo -->
      <div class="login-logo">
        <svg width="32" height="32" viewBox="0 0 20 20" fill="none" aria-hidden="true">
          <rect width="20" height="20" rx="5" fill="#1a1a1a"/>
          <rect x="4" y="4" width="5" height="5" rx="1.5" fill="white" opacity="0.9"/>
          <rect x="11" y="4" width="5" height="5" rx="1.5" fill="white" opacity="0.3"/>
          <rect x="4" y="11" width="5" height="5" rx="1.5" fill="white" opacity="0.3"/>
          <rect x="11" y="11" width="5" height="5" rx="1.5" fill="white" opacity="0.7"/>
        </svg>
        <span class="login-title">Central DSP</span>
      </div>

      <p class="login-subtitle">Sign in to your account</p>

      <form @submit.prevent="doLogin" class="login-form">
        <div class="field">
          <label for="email">Email</label>
          <input
            id="email"
            v-model="email"
            type="email"
            placeholder="admin@centraldsp.local"
            autocomplete="username"
            :disabled="loading"
            required
          />
        </div>
        <div class="field">
          <label for="password">Password</label>
          <input
            id="password"
            v-model="password"
            type="password"
            placeholder="••••••••"
            autocomplete="current-password"
            :disabled="loading"
            required
          />
        </div>

        <div v-if="error" class="login-error">{{ error }}</div>

        <button type="submit" class="login-btn" :disabled="loading">
          <span v-if="loading" class="spinner" />
          <span>{{ loading ? 'Signing in…' : 'Sign in' }}</span>
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../auth'

const router   = useRouter()
const { login } = useAuth()

const email    = ref('admin@centraldsp.local')
const password = ref('')
const loading  = ref(false)
const error    = ref('')

async function doLogin() {
  loading.value = true
  error.value   = ''
  try {
    await login(email.value, password.value)
    router.push('/')
  } catch (e) {
    error.value = e.message || 'Invalid credentials'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  min-height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #0a0a0a;
  padding: 24px;
}

.login-box {
  width: 100%;
  max-width: 360px;
  background: #111;
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 12px;
  padding: 36px 32px;
}

.login-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 24px;
}
.login-title {
  font-size: 15px;
  font-weight: 600;
  color: #d0d0d0;
  letter-spacing: -0.2px;
}
.login-subtitle {
  font-size: 13px;
  color: #555;
  margin-bottom: 24px;
}

.login-form { display: flex; flex-direction: column; gap: 14px; }

.field { display: flex; flex-direction: column; gap: 6px; }
.field label {
  font-size: 12px;
  font-weight: 500;
  color: #888;
  letter-spacing: 0.01em;
}
.field input {
  background: #0d0d0d;
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 7px;
  padding: 9px 12px;
  font-size: 13px;
  color: #d0d0d0;
  outline: none;
  transition: border-color 150ms;
}
.field input:focus { border-color: rgba(255,255,255,0.25); }
.field input::placeholder { color: #333; }
.field input:disabled { opacity: 0.5; cursor: not-allowed; }

.login-error {
  font-size: 12px;
  color: #f87171;
  background: rgba(248,113,113,0.08);
  border: 1px solid rgba(248,113,113,0.15);
  border-radius: 6px;
  padding: 8px 12px;
}

.login-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: #1f1f1f;
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 7px;
  padding: 10px 16px;
  font-size: 13px;
  font-weight: 500;
  color: #d0d0d0;
  cursor: pointer;
  transition: background 150ms, border-color 150ms;
  margin-top: 4px;
}
.login-btn:hover:not(:disabled) {
  background: #2a2a2a;
  border-color: rgba(255,255,255,0.2);
}
.login-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.spinner {
  width: 12px; height: 12px;
  border: 2px solid rgba(255,255,255,0.15);
  border-top-color: #888;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
