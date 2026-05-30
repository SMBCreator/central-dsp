<template>
  <div class="min-h-screen flex bg-[#0d1117]">

    <!-- Sidebar -->
    <aside class="w-56 flex-shrink-0 border-r border-[#30363d] flex flex-col">
      <!-- Logo -->
      <div class="h-14 flex items-center px-4 border-b border-[#30363d]">
        <span class="text-[#00d4ff] font-mono font-semibold text-lg glow-brand">
          ▶ Central DSP
        </span>
      </div>

      <!-- Nav -->
      <nav class="flex-1 py-3 overflow-y-auto">
        <div class="px-2 space-y-0.5">
          <NavItem to="/"          icon="📊" label="Dashboard" />
          <NavItem to="/campaigns" icon="📁" label="Campaigns" />
          <NavItem to="/pixels"    icon="📡" label="Pixel Center" />
          <NavItem to="/analytics" icon="📈" label="Analytics" />

          <div class="my-2 border-t border-[#30363d]" />
          <p class="px-2 py-1 text-[10px] font-medium text-[#8b949e] uppercase tracking-widest">RTB</p>

          <NavItem to="/rtb"        icon="⚡" label="Bidder" />
          <NavItem to="/creatives"  icon="🎨" label="Creatives" />
          <NavItem to="/targeting"  icon="🎯" label="Targeting" />

          <div class="my-2 border-t border-[#30363d]" />
          <p class="px-2 py-1 text-[10px] font-medium text-[#8b949e] uppercase tracking-widest">Automation</p>

          <NavItem to="/autopilot" icon="🤖" label="AutoPilot" />
          <NavItem to="/networks"  icon="🌐" label="Ad Networks" />
          <NavItem to="/settings" icon="⚙️"  label="Settings" />
        </div>
      </nav>

      <!-- Footer -->
      <div class="p-3 border-t border-[#30363d] text-[10px] text-[#8b949e] font-mono">
        v0.3.0 • RTB4FREE
      </div>
    </aside>

    <!-- Main content -->
    <main class="flex-1 flex flex-col min-h-screen overflow-hidden">
      <!-- Top bar -->
      <header class="h-14 flex items-center justify-between px-6 border-b border-[#30363d] flex-shrink-0">
        <h1 class="text-sm font-medium text-gray-300">
          {{ currentTitle }}
        </h1>
        <div class="flex items-center gap-3">
          <!-- Bidder status indicator -->
          <div class="flex items-center gap-1.5 text-xs font-mono">
            <span class="w-2 h-2 rounded-full" :class="bidderOnline ? 'bg-green-400 animate-pulse' : 'bg-red-500'"></span>
            <span class="text-[#8b949e]">Bidder {{ bidderOnline ? 'LIVE' : 'OFFLINE' }}</span>
          </div>
        </div>
      </header>

      <!-- Page view -->
      <div class="flex-1 overflow-auto p-6">
        <RouterView />
      </div>
    </main>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import NavItem from './components/NavItem.vue'

const route = useRoute()
const bidderOnline = ref(false)

const currentTitle = computed(() => route.meta?.title || 'Central DSP')

// Poll bidder health every 15 seconds
let healthTimer
async function checkBidderHealth() {
  try {
    const r = await fetch('http://localhost:8080/health', { signal: AbortSignal.timeout(3000) })
    bidderOnline.value = r.ok
  } catch {
    bidderOnline.value = false
  }
}

onMounted(() => {
  checkBidderHealth()
  healthTimer = setInterval(checkBidderHealth, 15000)
})
onUnmounted(() => clearInterval(healthTimer))
</script>
