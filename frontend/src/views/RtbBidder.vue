<template>
  <div class="space-y-6">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <h1 class="text-lg font-semibold">RTB Bidder <span class="text-[#8b949e] text-sm font-normal">(RTB4FREE)</span></h1>
      <div class="flex items-center gap-2">
        <span class="w-2 h-2 rounded-full" :class="online ? 'bg-green-400 animate-pulse' : 'bg-red-500'"></span>
        <span class="text-xs font-mono" :class="online ? 'text-green-400' : 'text-red-400'">
          {{ online ? 'LIVE' : 'OFFLINE' }}
        </span>
        <button class="btn btn-ghost text-xs" @click="testBidRequest">🧪 Test Bid</button>
      </div>
    </div>

    <!-- Today KPIs -->
    <div class="grid grid-cols-2 md:grid-cols-5 gap-3">
      <div class="kpi-card border-[#00d4ff]/20">
        <div class="kpi-value font-mono text-[#00d4ff]">{{ stats.total_auctions.toLocaleString() }}</div>
        <div class="kpi-label">Auctions Today</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-value font-mono">{{ stats.wins.toLocaleString() }}</div>
        <div class="kpi-label">Wins</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-value font-mono" :class="stats.win_rate > 1 ? 'text-green-400' : 'text-yellow-400'">
          {{ stats.win_rate }}%
        </div>
        <div class="kpi-label">Win Rate</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-value font-mono">${{ stats.avg_win_price }}</div>
        <div class="kpi-label">Avg Win Price</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-value font-mono text-red-400">${{ stats.total_spend }}</div>
        <div class="kpi-label">Total Spend</div>
      </div>
    </div>

    <!-- Bidder config -->
    <div class="card">
      <h2 class="text-sm font-medium mb-3">Bidder Endpoint</h2>
      <div class="font-mono text-xs text-[#00d4ff] bg-[#0d1117] rounded px-3 py-2 border border-[#30363d]">
        POST http://{{ hostname }}:8080/bidder  &nbsp;│&nbsp; OpenRTB 2.x
      </div>
      <div class="mt-3 grid grid-cols-2 gap-3 text-xs text-[#8b949e]">
        <div>Win Notice: <span class="text-gray-400">POST /api/rtb/win-notice</span></div>
        <div>Loss Notice: <span class="text-gray-400">POST /api/rtb/loss-notice</span></div>
        <div>Campaign Feed: <span class="text-gray-400">GET /api/rtb/campaigns</span></div>
        <div>Response Timeout: <span class="text-gray-400">100ms</span></div>
      </div>
    </div>

    <!-- Test result -->
    <div v-if="testResult" class="card">
      <h2 class="text-sm font-medium mb-2">Test Bid Response</h2>
      <pre class="text-xs font-mono text-green-400 overflow-auto max-h-64">{{ testResult }}</pre>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const online     = ref(false)
const testResult = ref('')
const hostname   = ref(window.location.hostname)

const stats = ref({
  total_auctions: 0, wins: 0,
  win_rate: 0, avg_win_price: '0.000000',
  total_spend: '0.0000',
})

async function checkHealth() {
  try {
    const r = await fetch(`http://${hostname.value}:8080/health`, { signal: AbortSignal.timeout(3000) })
    online.value = r.ok
  } catch { online.value = false }
}

async function testBidRequest() {
  testResult.value = 'Sending test bid request...'
  try {
    const r = await fetch(`http://${hostname.value}:8080/bidder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: `test-${Date.now()}`,
        imp: [{ id: '1', banner: { w: 300, h: 250 }, bidfloor: 0.01 }],
        site: { domain: 'test.example.com', page: 'https://test.example.com/' },
        device: { ua: navigator.userAgent, ip: '1.2.3.4', geo: { country: 'DEU' } },
      }),
    })
    const data = await r.json()
    testResult.value = JSON.stringify(data, null, 2)
  } catch (e) {
    testResult.value = `Error: ${e.message}`
  }
}

onMounted(() => {
  checkHealth()
  setInterval(checkHealth, 15000)
})
</script>
