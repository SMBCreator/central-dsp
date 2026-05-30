<template>
  <div class="space-y-6">
    <!-- Date filter -->
    <div class="flex items-center gap-2">
      <button v-for="p in periods" :key="p.value"
        class="btn text-xs"
        :class="period === p.value ? 'btn-primary' : 'btn-ghost'"
        @click="period = p.value">
        {{ p.label }}
      </button>
    </div>

    <!-- KPI row -->
    <div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3">
      <div class="kpi-card">
        <div class="kpi-value text-[#00d4ff]">{{ kpi.clicks.toLocaleString() }}</div>
        <div class="kpi-label">Clicks</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-value">{{ kpi.conversions.toLocaleString() }}</div>
        <div class="kpi-label">Conversions</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-value" :class="roiColor(kpi.roi)">{{ kpi.roi }}%</div>
        <div class="kpi-label">ROI</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-value text-red-400">${{ kpi.spend }}</div>
        <div class="kpi-label">Spend</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-value text-green-400">${{ kpi.revenue }}</div>
        <div class="kpi-label">Revenue</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-value">{{ kpi.cr }}%</div>
        <div class="kpi-label">CR</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-value">${{ kpi.epc }}</div>
        <div class="kpi-label">EPC</div>
      </div>
      <!-- RTB KPI -->
      <div class="kpi-card border-[#00d4ff]/30">
        <div class="kpi-value text-[#00d4ff]">{{ kpi.winRate }}%</div>
        <div class="kpi-label">Win Rate</div>
      </div>
    </div>

    <!-- Campaigns table -->
    <div class="card">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-sm font-medium">Campaigns</h2>
        <RouterLink to="/campaigns" class="text-xs text-[#00d4ff] hover:underline">All campaigns →</RouterLink>
      </div>

      <div v-if="loading" class="text-center py-8 text-[#8b949e] text-sm">Loading...</div>
      <div v-else-if="!campaigns.length" class="text-center py-8">
        <div class="text-3xl mb-2">📁</div>
        <p class="text-[#8b949e] text-sm">No campaigns yet</p>
        <RouterLink to="/campaigns" class="btn btn-primary mt-3 text-xs">Create first campaign</RouterLink>
      </div>
      <table v-else class="data-table">
        <thead>
          <tr>
            <th>Campaign</th>
            <th>Status</th>
            <th>Clicks</th>
            <th>Conv</th>
            <th>CR</th>
            <th>Spend</th>
            <th>Revenue</th>
            <th>ROI</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in campaigns" :key="c.id">
            <td class="font-sans font-medium text-gray-200">{{ c.name }}</td>
            <td><span :class="`badge-${c.status}`">{{ c.status }}</span></td>
            <td>{{ (c.clicks || 0).toLocaleString() }}</td>
            <td>{{ (c.conversions || 0).toLocaleString() }}</td>
            <td :class="roiColor(c.roi)">{{ c.cr || 0 }}%</td>
            <td class="text-red-400">${{ c.spend || '0.00' }}</td>
            <td class="text-green-400">${{ c.revenue || '0.00' }}</td>
            <td :class="roiColor(c.roi)" class="font-semibold">{{ c.roi || 0 }}%</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const period   = ref('today')
const loading  = ref(true)
const campaigns = ref([])

const periods = [
  { value: 'today',   label: 'Today' },
  { value: 'yesterday', label: 'Yesterday' },
  { value: '7d',      label: '7 days' },
  { value: '30d',     label: '30 days' },
]

const kpi = ref({
  clicks: 0, conversions: 0, roi: 0,
  spend: '0.00', revenue: '0.00',
  cr: 0, epc: '0.000', winRate: 0,
})

const roiColor = (v) => v > 0 ? 'roi-positive' : v < 0 ? 'roi-negative' : 'roi-neutral'

async function load() {
  loading.value = true
  try {
    // TODO: replace with real API calls in Phase 4 / Step 0
    const res = await fetch(`/api/campaigns`).catch(() => null)
    if (res && res.ok) {
      campaigns.value = await res.json()
    }
  } catch (e) {
    console.warn('Dashboard load failed:', e)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
