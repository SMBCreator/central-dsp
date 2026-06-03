<template>
  <div class="space-y-6">

    <div class="page-header">
      <div>
        <h1 class="page-title">Analytics</h1>
        <p class="page-subtitle">Detailed performance breakdown</p>
      </div>
      <button @click="exportCsv" class="btn btn-secondary">Export CSV</button>
    </div>

    <!-- Breakdown selector -->
    <div class="card">
      <div class="card-body">
        <div class="flex flex-wrap items-center gap-4">
          <div class="flex items-center gap-2">
            <label class="text-sm font-medium text-text-secondary">Group by</label>
            <select v-model="groupBy" class="select w-36">
              <option value="date">Date</option>
              <option value="campaign">Campaign</option>
              <option value="country">Country</option>
              <option value="device">Device</option>
              <option value="os">OS</option>
              <option value="site">Site</option>
            </select>
          </div>
          <div class="flex items-center gap-2">
            <label class="text-sm font-medium text-text-secondary">Metric</label>
            <select v-model="metric" class="select w-36">
              <option value="impressions">Impressions</option>
              <option value="clicks">Clicks</option>
              <option value="conversions">Conversions</option>
              <option value="spend">Spend</option>
              <option value="roi">ROI</option>
              <option value="win_rate">Win Rate</option>
            </select>
          </div>
        </div>
      </div>
    </div>

    <!-- Data table -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">Breakdown: {{ groupBy }}</span>
      </div>
      <div v-if="loading" class="card-body space-y-2">
        <div v-for="i in 10" :key="i" class="skeleton h-8 w-full rounded" />
      </div>
      <div v-else-if="!rows.length" class="empty-state">
        <div class="empty-title">No data available</div>
        <div class="empty-desc">Data will appear once your campaigns start running</div>
      </div>
      <div v-else class="overflow-x-auto">
        <table class="data-table">
          <thead>
            <tr>
              <th>{{ groupBy }}</th>
              <th>Impressions</th>
              <th>Clicks</th>
              <th>CTR</th>
              <th>Conversions</th>
              <th>CR</th>
              <th>Spend</th>
              <th>Revenue</th>
              <th>ROI</th>
              <th>Win Rate</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.key">
              <td class="font-medium">{{ row.key }}</td>
              <td class="tabnum">{{ fmt(row.impressions) }}</td>
              <td class="tabnum">{{ fmt(row.clicks) }}</td>
              <td class="tabnum">{{ row.ctr }}%</td>
              <td class="tabnum">{{ fmt(row.conversions) }}</td>
              <td class="tabnum" :class="row.cr > 2 ? 'val-positive' : ''">{{ row.cr }}%</td>
              <td class="tabnum text-text-secondary">${{ row.spend }}</td>
              <td class="tabnum val-positive">${{ row.revenue }}</td>
              <td class="tabnum font-semibold" :class="row.roi >= 0 ? 'val-positive' : 'val-negative'">{{ row.roi }}%</td>
              <td class="tabnum val-accent">{{ row.win_rate }}%</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'

const props   = defineProps({ period: String })
const loading = ref(true)
const rows    = ref([])
const groupBy = ref('date')
const metric  = ref('impressions')

const fmt = (n) => Number(n || 0).toLocaleString('en-US')

async function load() {
  loading.value = true
  try {
    const res = await fetch(
      `/api/analytics?group_by=${groupBy.value}&period=${props.period || '7d'}`
    )
    if (res.ok) rows.value = await res.json()
    else rows.value = []
  } catch { rows.value = [] }
  finally { loading.value = false }
}

function exportCsv() {
  const headers = ['Key','Impressions','Clicks','CTR','Conversions','CR','Spend','Revenue','ROI','Win Rate']
  const lines = [headers.join(','), ...rows.value.map(r =>
    [r.key, r.impressions, r.clicks, r.ctr, r.conversions, r.cr, r.spend, r.revenue, r.roi, r.win_rate].join(',')
  )]
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `analytics-${groupBy.value}-${props.period || '7d'}.csv`
  a.click()
}

onMounted(load)
watch([() => props.period, groupBy, metric], load)
</script>
