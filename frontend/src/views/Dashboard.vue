<template>
  <div class="page-wrap">

    <!-- Header -->
    <div class="page-header">
      <div>
        <h1 class="page-title">Dashboard</h1>
        <p class="page-subtitle">Overview across all active campaigns</p>
      </div>
      <RouterLink to="/campaigns" class="btn btn-primary">New Campaign</RouterLink>
    </div>

    <!-- KPI strip -->
    <div class="kpi-grid">
      <div class="kpi-cell">
        <div class="kpi-label">Impressions</div>
        <div class="kpi-value">{{ fmt(kpi.impressions) }}</div>
        <div :class="['kpi-delta', deltaClass(kpi.impDelta)]">
          {{ signedPct(kpi.impDelta) }}
        </div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Clicks</div>
        <div class="kpi-value">{{ fmt(kpi.clicks) }}</div>
        <div :class="['kpi-delta', deltaClass(kpi.clickDelta)]">
          {{ signedPct(kpi.clickDelta) }}
        </div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Conversions</div>
        <div class="kpi-value">{{ fmt(kpi.conversions) }}</div>
        <div class="kpi-delta flat">CTR {{ kpi.ctr }}%</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Spend</div>
        <div class="kpi-value">${{ kpi.spend }}</div>
        <div class="kpi-delta flat">CPM ${{ kpi.cpm }}</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Revenue</div>
        <div class="kpi-value val-positive">${{ kpi.revenue }}</div>
        <div class="kpi-delta flat">CPA ${{ kpi.cpa }}</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">ROI</div>
        <div :class="['kpi-value', kpi.roi >= 0 ? 'val-positive' : 'val-negative']">{{ kpi.roi }}%</div>
        <div class="kpi-delta flat">vs prev period</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Win Rate</div>
        <div class="kpi-value val-accent">{{ kpi.winRate }}%</div>
        <div class="kpi-delta flat">{{ fmt(kpi.bids) }} bids</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Avg CPM</div>
        <div class="kpi-value">${{ kpi.cpm }}</div>
        <div class="kpi-delta flat">Bid avg</div>
      </div>
    </div>

    <!-- Active campaigns -->
    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">Active Campaigns</div>
        </div>
        <RouterLink to="/campaigns" class="btn btn-ghost btn-sm">View all</RouterLink>
      </div>

      <div v-if="loading" class="card-body space-y-2">
        <div v-for="i in 4" :key="i" class="skeleton" style="height:40px" />
      </div>

      <div v-else-if="!campaigns.length" class="empty-state">
        <div class="empty-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path d="M3 6h18M3 12h18M3 18h12"/>
          </svg>
        </div>
        <div class="empty-title">No campaigns yet</div>
        <div class="empty-desc">Create your first campaign to start serving impressions</div>
        <RouterLink to="/campaigns" class="btn btn-secondary btn-sm" style="margin-top:16px">
          Create campaign
        </RouterLink>
      </div>

      <div v-else class="overflow-x-auto">
        <table class="data-table">
          <thead>
            <tr>
              <th>Campaign</th>
              <th>Status</th>
              <th>Impressions</th>
              <th>Clicks</th>
              <th>CTR</th>
              <th>Conv</th>
              <th>Spend</th>
              <th>Revenue</th>
              <th>ROI</th>
              <th>Win Rate</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in campaigns" :key="c.id">
              <td>
                <div style="font-weight:500;color:#d0d0d0">{{ c.name }}</div>
                <div class="text-xs val-muted">{{ c.type || 'RTB' }}</div>
              </td>
              <td><span :class="`badge badge-${c.status}`">{{ c.status }}</span></td>
              <td class="tabnum">{{ fmt(c.impressions || 0) }}</td>
              <td class="tabnum">{{ fmt(c.clicks || 0) }}</td>
              <td class="tabnum" :class="(c.ctr||0) > 1 ? 'val-positive' : ''">
                {{ (c.ctr || 0).toFixed(2) }}%
              </td>
              <td class="tabnum">{{ fmt(c.conversions || 0) }}</td>
              <td class="tabnum val-muted">${{ c.spend || '0.00' }}</td>
              <td class="tabnum val-positive">${{ c.revenue || '0.00' }}</td>
              <td class="tabnum" :class="(c.roi||0) >= 0 ? 'val-positive' : 'val-negative'">
                {{ c.roi || 0 }}%
              </td>
              <td class="tabnum val-accent">{{ c.win_rate || 0 }}%</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- RTB bidder status -->
    <div class="card">
      <div class="card-body" style="display:flex;align-items:center;justify-content:space-between;gap:16px">
        <div style="display:flex;align-items:center;gap:12px">
          <span :class="['status-dot', bidderStatus.online ? 'online' : 'offline']" />
          <div>
            <div style="font-size:13px;font-weight:600;color:#d0d0d0">
              RTB Bidder — {{ bidderStatus.online ? 'Online' : 'Offline' }}
            </div>
            <div style="font-size:11px;color:#444;margin-top:2px">
              {{ bidderStatus.online
                ? `${fmt(bidderStatus.requests)} bid requests processed`
                : 'Bidder service unreachable' }}
            </div>
          </div>
        </div>
        <RouterLink to="/rtb" class="btn btn-secondary btn-sm">Open Bidder</RouterLink>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'

const props   = defineProps({ period: String })
const loading  = ref(true)
const campaigns = ref([])

const kpi = ref({
  impressions:0, impDelta:0,
  clicks:0,      clickDelta:0,
  conversions:0, ctr:'0.00',
  spend:'0.00',  revenue:'0.00',
  roi:0,         winRate:'0.0',
  cpm:'0.00',    cpa:'0.00',
  bids:0,
})
const bidderStatus = ref({ online:false, requests:0 })

const fmt       = n  => Number(n).toLocaleString('en-US')
const signedPct = d  => (d >= 0 ? '+' : '') + d + '%'
const deltaClass = d => d > 0 ? 'up' : d < 0 ? 'down' : 'flat'

async function load() {
  loading.value = true
  try {
    const [c, s, b] = await Promise.allSettled([
      fetch(`/api/campaigns?period=${props.period||'7d'}`),
      fetch(`/api/stats/summary?period=${props.period||'7d'}`),
      fetch('/api/rtb/status'),
    ])
    if (c.status==='fulfilled' && c.value.ok) campaigns.value = await c.value.json()
    if (s.status==='fulfilled' && s.value.ok) Object.assign(kpi.value, await s.value.json())
    if (b.status==='fulfilled' && b.value.ok) {
      const bd = await b.value.json()
      bidderStatus.value = { online:true, requests:bd.requests||0 }
    }
  } catch(e) { console.warn(e) }
  finally { loading.value = false }
}

onMounted(load)
watch(() => props.period, load)
</script>
