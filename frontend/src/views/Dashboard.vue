<template>
  <div class="space-y-6">

    <!-- Page header -->
    <div class="page-header">
      <div>
        <h1 class="page-title">Dashboard</h1>
        <p class="page-subtitle">Performance overview across all campaigns</p>
      </div>
      <RouterLink to="/campaigns" class="btn btn-primary">
        New Campaign
      </RouterLink>
    </div>

    <!-- KPI row -->
    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label">Impressions</div>
        <div class="kpi-value">{{ fmt(kpi.impressions) }}</div>
        <div :class="['kpi-delta', kpi.impDelta >= 0 ? 'up' : 'down']">
          {{ kpi.impDelta >= 0 ? '+' : '' }}{{ kpi.impDelta }}%
        </div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Clicks</div>
        <div class="kpi-value">{{ fmt(kpi.clicks) }}</div>
        <div :class="['kpi-delta', kpi.clickDelta >= 0 ? 'up' : 'down']">
          {{ kpi.clickDelta >= 0 ? '+' : '' }}{{ kpi.clickDelta }}%
        </div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Conversions</div>
        <div class="kpi-value">{{ fmt(kpi.conversions) }}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Spend</div>
        <div class="kpi-value">${{ kpi.spend }}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Revenue</div>
        <div class="kpi-value text-positive">${{ kpi.revenue }}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">ROI</div>
        <div :class="['kpi-value', kpi.roi >= 0 ? 'text-positive' : 'text-negative']">
          {{ kpi.roi }}%
        </div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Win Rate</div>
        <div class="kpi-value val-accent">{{ kpi.winRate }}%</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Avg CPM</div>
        <div class="kpi-value">${{ kpi.cpm }}</div>
      </div>
    </div>

    <!-- Campaigns table -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">Active Campaigns</span>
        <RouterLink to="/campaigns" class="text-xs text-accent font-medium hover:underline">
          View all
        </RouterLink>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="card-body space-y-2">
        <div v-for="i in 5" :key="i" class="skeleton h-10 w-full rounded-md" />
      </div>

      <!-- Empty -->
      <div v-else-if="!campaigns.length" class="empty-state">
        <div class="empty-icon">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="1.5" class="text-text-muted">
            <path d="M3 7h18M3 12h18M3 17h12"/>
          </svg>
        </div>
        <div class="empty-title">No campaigns yet</div>
        <div class="empty-desc mb-4">Create your first campaign to start driving traffic</div>
        <RouterLink to="/campaigns" class="btn btn-primary btn-sm">Create campaign</RouterLink>
      </div>

      <!-- Table -->
      <div v-else class="overflow-x-auto">
        <table class="data-table">
          <thead>
            <tr>
              <th>Campaign</th>
              <th>Status</th>
              <th>Impressions</th>
              <th>Clicks</th>
              <th>Conv</th>
              <th>CR</th>
              <th>Spend</th>
              <th>Revenue</th>
              <th>ROI</th>
              <th>Win Rate</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in campaigns" :key="c.id">
              <td>
                <div class="font-medium text-text-primary">{{ c.name }}</div>
                <div class="text-xs text-text-muted">{{ c.type || 'RTB' }}</div>
              </td>
              <td><span :class="`badge-${c.status}`">{{ c.status }}</span></td>
              <td class="tabnum">{{ fmt(c.impressions || 0) }}</td>
              <td class="tabnum">{{ fmt(c.clicks || 0) }}</td>
              <td class="tabnum">{{ fmt(c.conversions || 0) }}</td>
              <td class="tabnum" :class="c.cr > 2 ? 'val-positive' : ''">
                {{ (c.cr || 0).toFixed(2) }}%
              </td>
              <td class="tabnum text-text-secondary">${{ c.spend || '0.00' }}</td>
              <td class="tabnum val-positive">${{ c.revenue || '0.00' }}</td>
              <td class="tabnum font-semibold"
                  :class="(c.roi||0) >= 0 ? 'val-positive' : 'val-negative'">
                {{ c.roi || 0 }}%
              </td>
              <td class="tabnum val-accent">{{ c.win_rate || 0 }}%</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- RTB Bidder status strip -->
    <div class="card">
      <div class="card-body flex items-center justify-between">
        <div class="flex items-center gap-3">
          <span :class="['status-dot', bidderStatus.online ? 'online' : 'offline']"></span>
          <div>
            <div class="text-sm font-semibold text-text-primary">
              RTB Bidder — {{ bidderStatus.online ? 'Online' : 'Offline' }}
            </div>
            <div class="text-xs text-text-muted">
              {{ bidderStatus.online
                  ? `${fmt(bidderStatus.requests)} requests processed`
                  : 'Check bidder service' }}
            </div>
          </div>
        </div>
        <RouterLink to="/rtb" class="btn btn-secondary btn-sm">View Bidder</RouterLink>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'

const props  = defineProps({ period: String })
const loading = ref(true)
const campaigns = ref([])

const kpi = ref({
  impressions: 0, impDelta: 0,
  clicks: 0,      clickDelta: 0,
  conversions: 0, spend: '0.00',
  revenue: '0.00', roi: 0,
  winRate: 0,      cpm: '0.00',
})

const bidderStatus = ref({ online: false, requests: 0 })

const fmt = (n) => Number(n).toLocaleString('en-US')

async function load() {
  loading.value = true
  try {
    const [campRes, statsRes, bidderRes] = await Promise.allSettled([
      fetch(`/api/campaigns?period=${props.period || '7d'}`),
      fetch(`/api/stats/summary?period=${props.period || '7d'}`),
      fetch('/api/rtb/status'),
    ])
    if (campRes.status === 'fulfilled' && campRes.value.ok) {
      campaigns.value = await campRes.value.json()
    }
    if (statsRes.status === 'fulfilled' && statsRes.value.ok) {
      const s = await statsRes.value.json()
      Object.assign(kpi.value, s)
    }
    if (bidderRes.status === 'fulfilled' && bidderRes.value.ok) {
      const b = await bidderRes.value.json()
      bidderStatus.value = { online: true, requests: b.requests || 0 }
    }
  } catch (e) {
    console.warn('Dashboard load error:', e)
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => props.period, load)
</script>
