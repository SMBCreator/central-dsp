<template>
  <!-- Login page: no sidebar, full page -->
  <RouterView v-if="isLoginRoute" />

  <!-- App shell -->
  <div v-else :class="['layout', theme]">

    <!-- ══ SIDEBAR ══════════════════════════════════════════ -->
    <aside class="sidebar">

      <!-- Logo -->
      <div class="sidebar-logo">
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
          <rect width="20" height="20" rx="5" fill="currentColor" opacity="0.15"/>
          <rect x="4" y="4" width="5" height="5" rx="1.5" fill="currentColor" opacity="0.9"/>
          <rect x="11" y="4" width="5" height="5" rx="1.5" fill="currentColor" opacity="0.3"/>
          <rect x="4" y="11" width="5" height="5" rx="1.5" fill="currentColor" opacity="0.3"/>
          <rect x="11" y="11" width="5" height="5" rx="1.5" fill="currentColor" opacity="0.7"/>
        </svg>
        <span class="logo-text">Central DSP</span>
      </div>

      <!-- Navigation -->
      <nav class="sidebar-nav">
        <NavItem to="/"          icon="grid"        label="Dashboard" />

        <div class="section-label">TRACKING</div>
        <NavItem to="/campaigns" icon="link"        label="SmartLinks" />
        <NavItem to="/analytics" icon="bar-chart-2" label="Analytics" />
        <NavItem to="/pixels"    icon="crosshair"   label="Pixel Center" />

        <div class="section-label">RTB</div>
        <NavItem to="/rtb"       icon="zap"         label="RTB Campaigns" />
        <NavItem to="/creatives" icon="image"        label="Creatives" />
        <NavItem to="/targeting" icon="filter"       label="Targeting" />

        <div class="section-label">SYSTEM</div>
        <NavItem to="/autopilot" icon="cpu"          label="AutoPilot" />
        <NavItem to="/networks"  icon="globe"        label="Ad Networks" />
        <NavItem to="/settings"  icon="settings"     label="Settings" />
      </nav>

      <!-- Footer -->
      <div class="sidebar-footer">
        <div class="flex items-center gap-2">
          <span :class="['status-dot', bidderOnline ? 'online' : 'offline']" />
          <span class="status-label">
            Bidder
            <span :class="bidderOnline ? 'status-live' : 'status-off'">
              {{ bidderOnline ? 'Live' : 'Offline' }}
            </span>
          </span>
        </div>
        <!-- Theme toggle -->
        <button @click="toggleTheme" class="theme-toggle" :title="theme === 'dark' ? 'Switch to light' : 'Switch to dark'">
          <!-- Sun -->
          <svg v-if="theme === 'dark'" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="5"/>
            <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>
          </svg>
          <!-- Moon -->
          <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
          </svg>
        </button>
      </div>
    </aside>

    <!-- ══ MAIN ═════════════════════════════════════════════ -->
    <div class="main-wrap">

      <!-- Topbar -->
      <header class="topbar">
        <div class="topbar-left">
          <span class="topbar-breadcrumb">Central DSP</span>
          <span class="topbar-sep">/</span>
          <span class="topbar-page">{{ currentTitle }}</span>
        </div>
        <div class="topbar-right">
          <div class="period-switcher">
            <button
              v-for="p in periods" :key="p.v"
              @click="activePeriod = p.v"
              :class="['period-btn', activePeriod === p.v ? 'active' : '']">
              {{ p.l }}
            </button>
          </div>
          <div class="avatar" @click="logout">{{ userInitials }}</div>
        </div>
      </header>

      <!-- Content -->
      <main class="main-content">
        <RouterView :period="activePeriod" />
      </main>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import NavItem from './components/NavItem.vue'
import { useAuth } from './auth'

const route  = useRoute()
const router = useRouter()
const { state, logout: authLogout, isLoggedIn } = useAuth()

const bidderOnline = ref(false)
const activePeriod = ref('7d')
const theme        = ref(window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')

const periods = [
  { v: 'today', l: 'Today' },
  { v: '7d',    l: '7 days' },
  { v: '30d',   l: '30 days' },
]

const isLoginRoute = computed(() => route.path === '/login')
const currentTitle = computed(() => route.meta?.title || 'Dashboard')
const userInitials = computed(() => {
  const name = state.user?.full_name || state.user?.email || 'AD'
  return name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2)
})

function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
}

function logout() {
  authLogout()
  router.push('/login')
}

let healthTimer
async function checkBidderHealth() {
  try {
    const r = await fetch('/api/rtb/status', { signal: AbortSignal.timeout(3000) })
    bidderOnline.value = r.ok
  } catch {
    bidderOnline.value = false
  }
}

onMounted(() => { checkBidderHealth(); healthTimer = setInterval(checkBidderHealth, 15000) })
onUnmounted(() => clearInterval(healthTimer))
</script>

<style scoped>
/* ── THEMES ── */
.layout.dark  { --bg: #0a0a0a; --sidebar-bg: #0d0d0d; --sidebar-border: rgba(255,255,255,0.06); --topbar-bg: #0a0a0a; --text: #d0d0d0; --text-muted: #555; --text-faint: #333; --section-label: #2e2e2e; --hover-bg: rgba(255,255,255,0.05); --active-bg: rgba(255,255,255,0.08); --active-text: #e0e0e0; --period-bg: rgba(255,255,255,0.04); --period-border: rgba(255,255,255,0.06); --period-active: rgba(255,255,255,0.08); --avatar-bg: #1f1f1f; --avatar-border: rgba(255,255,255,0.1); --dot-online: #4ade80; --dot-offline: #555; }
.layout.light { --bg: #f4f4f2; --sidebar-bg: #ffffff; --sidebar-border: rgba(0,0,0,0.08); --topbar-bg: #ffffff; --text: #1a1a1a; --text-muted: #888; --text-faint: #bbb; --section-label: #ccc; --hover-bg: rgba(0,0,0,0.04); --active-bg: rgba(0,0,0,0.07); --active-text: #111; --period-bg: rgba(0,0,0,0.04); --period-border: rgba(0,0,0,0.07); --period-active: rgba(0,0,0,0.08); --avatar-bg: #f0f0ee; --avatar-border: rgba(0,0,0,0.1); --dot-online: #16a34a; --dot-offline: #bbb; }

.layout {
  display: flex;
  min-height: 100dvh;
  background: var(--bg);
  color: var(--text);
  transition: background 200ms, color 200ms;
}

/* ── SIDEBAR ── */
.sidebar {
  width: 212px;
  flex-shrink: 0;
  background: var(--sidebar-bg);
  border-right: 1px solid var(--sidebar-border);
  display: flex;
  flex-direction: column;
  position: fixed;
  inset: 0 auto 0 0;
  z-index: 30;
  transition: background 200ms, border-color 200ms;
}

.sidebar-logo {
  height: 52px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 18px;
  border-bottom: 1px solid var(--sidebar-border);
  flex-shrink: 0;
  color: var(--text);
}
.logo-text {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  letter-spacing: -0.2px;
}

.sidebar-nav {
  flex: 1;
  overflow-y: auto;
  padding: 10px;
}

.section-label {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--section-label);
  padding: 12px 8px 4px;
  text-transform: uppercase;
}

.sidebar-footer {
  padding: 12px 14px;
  border-top: 1px solid var(--sidebar-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.status-dot {
  width: 6px; height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.status-dot.online  { background: var(--dot-online); box-shadow: 0 0 5px var(--dot-online); }
.status-dot.offline { background: var(--dot-offline); }
.status-label { font-size: 11px; color: var(--text-muted); display:flex; gap:4px; align-items:center; }
.status-live  { color: var(--dot-online); }
.status-off   { color: var(--dot-offline); }

.theme-toggle {
  width: 28px; height: 28px;
  border-radius: 7px;
  background: var(--hover-bg);
  border: 1px solid var(--sidebar-border);
  display: flex; align-items: center; justify-content: center;
  color: var(--text-muted);
  cursor: pointer;
  transition: background 150ms, color 150ms;
}
.theme-toggle:hover { background: var(--active-bg); color: var(--text); }

/* ── TOPBAR ── */
.main-wrap {
  flex: 1;
  display: flex;
  flex-direction: column;
  margin-left: 212px;
  min-width: 0;
  min-height: 100dvh;
}

.topbar {
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  border-bottom: 1px solid var(--sidebar-border);
  background: var(--topbar-bg);
  position: sticky;
  top: 0;
  z-index: 20;
  flex-shrink: 0;
  transition: background 200ms, border-color 200ms;
}

.topbar-left { display: flex; align-items: center; gap: 6px; }
.topbar-breadcrumb { font-size: 12px; color: var(--text-faint); }
.topbar-sep        { font-size: 12px; color: var(--section-label); }
.topbar-page       { font-size: 12px; font-weight: 500; color: var(--text-muted); }

.topbar-right { display: flex; align-items: center; gap: 12px; }

.period-switcher {
  display: flex;
  align-items: center;
  background: var(--period-bg);
  border-radius: 7px;
  padding: 3px;
  gap: 1px;
  border: 1px solid var(--period-border);
}
.period-btn {
  padding: 4px 10px;
  border-radius: 5px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-muted);
  background: transparent;
  border: none;
  cursor: pointer;
  transition: all 120ms;
}
.period-btn:hover { color: var(--text); }
.period-btn.active {
  background: var(--period-active);
  color: var(--text);
}

.avatar {
  width: 28px; height: 28px;
  border-radius: 50%;
  background: var(--avatar-bg);
  border: 1px solid var(--avatar-border);
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 600;
  color: var(--text-muted);
  letter-spacing: 0.02em;
  cursor: pointer;
  user-select: none;
  transition: background 150ms;
}
.avatar:hover { background: var(--active-bg); }

.main-content {
  flex: 1;
  overflow: auto;
  padding: 24px;
  background: var(--bg);
  transition: background 200ms;
}

.flex { display:flex; }
.items-center { align-items:center; }
.gap-2 { gap:8px; }
</style>
