<template>
  <div class="min-h-screen flex bg-surface-page">

    <!-- ── Sidebar ─────────────────────────────────────── -->
    <aside
      class="w-[220px] flex-shrink-0 border-r border-surface-border
             bg-surface flex flex-col fixed left-0 top-0 bottom-0 z-30">

      <!-- Logo -->
      <div class="h-14 flex items-center px-5 border-b border-surface-border">
        <div class="flex items-center gap-2.5">
          <!-- Wordmark SVG logo -->
          <svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">
            <rect width="22" height="22" rx="6" fill="#18181b"/>
            <rect x="5" y="5" width="5" height="5" rx="1.5" fill="white" opacity="0.9"/>
            <rect x="12" y="5" width="5" height="5" rx="1.5" fill="white" opacity="0.5"/>
            <rect x="5" y="12" width="5" height="5" rx="1.5" fill="white" opacity="0.5"/>
            <rect x="12" y="12" width="5" height="5" rx="1.5" fill="#a5b4fc" opacity="0.9"/>
          </svg>
          <span class="text-sm font-semibold text-text-primary tracking-tight">Central DSP</span>
        </div>
      </div>

      <!-- Nav -->
      <nav class="flex-1 py-3 overflow-y-auto px-3 space-y-0.5">

        <NavItem to="/"          label="Dashboard" />
        <NavItem to="/campaigns" label="Campaigns" />
        <NavItem to="/analytics" label="Analytics" />
        <NavItem to="/pixels"    label="Pixel Center" />

        <div class="section-label">RTB Bidder</div>

        <NavItem to="/rtb"       label="Overview" />
        <NavItem to="/creatives" label="Creatives" />
        <NavItem to="/targeting" label="Targeting" />

        <div class="section-label">Automation</div>

        <NavItem to="/autopilot" label="AutoPilot" />
        <NavItem to="/networks"  label="Ad Networks" />
        <NavItem to="/settings"  label="Settings" />

      </nav>

      <!-- Bidder status + version -->
      <div class="p-4 border-t border-surface-border">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span :class="['status-dot', bidderOnline ? 'online' : 'offline']"></span>
            <span class="text-xs text-text-secondary">
              Bidder {{ bidderOnline ? 'Live' : 'Offline' }}
            </span>
          </div>
          <span class="text-2xs text-text-muted font-mono">v1.0</span>
        </div>
      </div>
    </aside>

    <!-- ── Main ───────────────────────────────────────── -->
    <div class="flex-1 flex flex-col ml-[220px] min-h-screen">

      <!-- Top bar -->
      <header class="h-14 flex items-center justify-between px-6
                     border-b border-surface-border bg-surface flex-shrink-0 sticky top-0 z-20">
        <div class="flex items-center gap-2">
          <!-- Breadcrumb -->
          <span class="text-sm text-text-muted">Central DSP</span>
          <span class="text-text-muted text-sm">/</span>
          <span class="text-sm font-medium text-text-primary">{{ currentTitle }}</span>
        </div>

        <div class="flex items-center gap-3">
          <!-- Period selector -->
          <div class="flex items-center gap-1 bg-surface-hover rounded-md p-1">
            <button v-for="p in periods" :key="p.v"
              @click="activePeriod = p.v"
              :class="['px-3 py-1 text-xs font-medium rounded transition-all duration-150',
                activePeriod === p.v
                  ? 'bg-surface shadow-sm text-text-primary'
                  : 'text-text-muted hover:text-text-primary'
              ]">
              {{ p.l }}
            </button>
          </div>

          <!-- Avatar -->
          <div class="w-8 h-8 rounded-full bg-brand flex items-center justify-center">
            <span class="text-xs font-semibold text-white">AD</span>
          </div>
        </div>
      </header>

      <!-- Page -->
      <main class="flex-1 overflow-auto p-6">
        <RouterView :period="activePeriod" />
      </main>
    </div>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import NavItem from './components/NavItem.vue'

const route        = useRoute()
const bidderOnline = ref(false)
const activePeriod = ref('7d')

const periods = [
  { v: 'today', l: 'Today' },
  { v: '7d',    l: '7d' },
  { v: '30d',   l: '30d' },
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
