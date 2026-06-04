<template>
  <div class="layout">

    <!-- ══ SIDEBAR ══════════════════════════════════════════ -->
    <aside class="sidebar">

      <!-- Logo -->
      <div class="sidebar-logo">
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
          <rect width="20" height="20" rx="5" fill="#1a1a1a"/>
          <rect x="4" y="4" width="5" height="5" rx="1.5" fill="white" opacity="0.9"/>
          <rect x="11" y="4" width="5" height="5" rx="1.5" fill="white" opacity="0.3"/>
          <rect x="4" y="11" width="5" height="5" rx="1.5" fill="white" opacity="0.3"/>
          <rect x="11" y="11" width="5" height="5" rx="1.5" fill="white" opacity="0.7"/>
        </svg>
        <span class="logo-text">Central DSP</span>
      </div>

      <!-- Navigation -->
      <nav class="sidebar-nav">
        <NavItem to="/"          label="Dashboard" />
        <NavItem to="/campaigns" label="Campaigns" />
        <NavItem to="/analytics" label="Analytics" />
        <NavItem to="/pixels"    label="Pixel Center" />

        <div class="section-label">RTB</div>
        <NavItem to="/rtb"       label="Bidder" />
        <NavItem to="/creatives" label="Creatives" />
        <NavItem to="/targeting" label="Targeting" />

        <div class="section-label">System</div>
        <NavItem to="/autopilot" label="AutoPilot" />
        <NavItem to="/networks"  label="Ad Networks" />
        <NavItem to="/settings"  label="Settings" />
      </nav>

      <!-- Footer -->
      <div class="sidebar-footer">
        <div class="flex items-center gap-2">
          <span :class="['status-dot', bidderOnline ? 'online' : 'offline']" />
          <span class="text-xs" style="color:#444">
            Bidder&nbsp;<span :style="bidderOnline ? 'color:#4ade80' : 'color:#555'">
              {{ bidderOnline ? 'Live' : 'Offline' }}
            </span>
          </span>
        </div>
        <span style="font-size:10px;color:#333;font-family:'JetBrains Mono',monospace">v1.0</span>
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
          <div class="avatar">AD</div>
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
import { useRoute } from 'vue-router'
import NavItem from './components/NavItem.vue'

const route = useRoute()
const bidderOnline = ref(false)
const activePeriod = ref('7d')

const periods = [
  { v: 'today', l: 'Today' },
  { v: '7d',    l: '7 days' },
  { v: '30d',   l: '30 days' },
]

const currentTitle = computed(() => route.meta?.title || 'Dashboard')

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
.layout {
  display: flex;
  min-height: 100dvh;
  background: #0a0a0a;
}

/* ── SIDEBAR ── */
.sidebar {
  width: 212px;
  flex-shrink: 0;
  background: #0d0d0d;
  border-right: 1px solid rgba(255,255,255,0.06);
  display: flex;
  flex-direction: column;
  position: fixed;
  inset: 0 auto 0 0;
  z-index: 30;
}

.sidebar-logo {
  height: 52px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 18px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
  flex-shrink: 0;
}
.logo-text {
  font-size: 13px;
  font-weight: 600;
  color: #d0d0d0;
  letter-spacing: -0.2px;
}

.sidebar-nav {
  flex: 1;
  overflow-y: auto;
  padding: 10px 10px;
}

.sidebar-footer {
  padding: 14px 18px;
  border-top: 1px solid rgba(255,255,255,0.05);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

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
  border-bottom: 1px solid rgba(255,255,255,0.06);
  background: #0a0a0a;
  position: sticky;
  top: 0;
  z-index: 20;
  flex-shrink: 0;
}

.topbar-left { display: flex; align-items: center; gap: 6px; }
.topbar-breadcrumb { font-size: 12px; color: #333; }
.topbar-sep        { font-size: 12px; color: #2a2a2a; }
.topbar-page       { font-size: 12px; font-weight: 500; color: #888; }

.topbar-right { display: flex; align-items: center; gap: 12px; }

.period-switcher {
  display: flex;
  align-items: center;
  background: rgba(255,255,255,0.04);
  border-radius: 7px;
  padding: 3px;
  gap: 1px;
  border: 1px solid rgba(255,255,255,0.06);
}
.period-btn {
  padding: 4px 10px;
  border-radius: 5px;
  font-size: 12px;
  font-weight: 500;
  color: #444;
  background: transparent;
  border: none;
  cursor: pointer;
  transition: all 120ms;
}
.period-btn:hover { color: #888; }
.period-btn.active {
  background: rgba(255,255,255,0.08);
  color: #d0d0d0;
}

.avatar {
  width: 28px; height: 28px;
  border-radius: 50%;
  background: #1f1f1f;
  border: 1px solid rgba(255,255,255,0.1);
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 600;
  color: #888;
  letter-spacing: 0.02em;
}

.main-content {
  flex: 1;
  overflow: auto;
  padding: 24px;
}
</style>
