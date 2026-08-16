<template>
  <div class="page-wrap">

    <!-- Header -->
    <div class="page-header">
      <div>
        <h1 class="page-title">Analytics</h1>
        <p class="page-subtitle">Performance breakdown across all dimensions</p>
      </div>
      <button @click="exportCsv" class="btn btn-secondary">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:6px">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
          <polyline points="7 10 12 15 17 10"/>
          <line x1="12" y1="15" x2="12" y2="3"/>
        </svg>
        Export CSV
      </button>
    </div>

    <!-- KPI Strip -->
    <div class="kpi-grid" style="grid-template-columns:repeat(5,1fr)">
      <div v-for="k in kpis" :key="k.id" class="kpi-cell">
        <div class="kpi-label">{{ k.label }}</div>
        <div class="kpi-value">{{ k.value }}</div>
        <div class="kpi-delta" :class="k.up ? 'up' : 'down'">
          <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <polyline v-if="k.up" points="18 15 12 9 6 15"/>
            <polyline v-else     points="6 9 12 15 18 9"/>
          </svg>
          {{ k.delta }}
        </div>
        <!-- sparkline -->
        <svg v-if="k.spark.length" :viewBox="`0 0 ${k.spark.length*8} 32`" class="sparkline"
          :style="{width: k.spark.length*8+'px'}" preserveAspectRatio="none">
          <polyline :points="sparkPoints(k.spark)" fill="none"
            :stroke="k.up ? '#34d399' : '#f87171'" stroke-width="1.5"
            stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
    </div>

    <!-- Dimension Tabs + Controls -->
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">
      <div class="chip-group">
        <button v-for="d in dimensions" :key="d.v"
          @click="groupBy = d.v"
          :class="['chip', groupBy === d.v ? 'active' : '']">
          {{ d.l }}
        </button>
      </div>
      <div style="display:flex;align-items:center;gap:8px">
        <div style="position:relative">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            style="position:absolute;left:10px;top:50%;transform:translateY(-50%);color:#444;pointer-events:none">
            <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
          </svg>
          <input v-model="search" class="input" style="padding-left:30px;width:200px" placeholder="Filter rows..." />
        </div>
      </div>
    </div>

    <!-- Table -->
    <div class="card">
      <div v-if="loading" style="padding:20px;display:flex;flex-direction:column;gap:8px">
        <div v-for="i in 8" :key="i" class="skeleton" style="height:40px" />
      </div>

      <div v-else-if="!visibleRows.length" class="empty-state">
        <div class="empty-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/>
          </svg>
        </div>
        <div class="empty-title">No data yet</div>
        <div class="empty-desc">Data appears once campaigns start delivering</div>
      </div>

      <div v-else class="overflow-x-auto">
        <table class="data-table">
          <thead>
            <tr>
              <th @click="sortBy('key')" class="sortable">
                {{ groupByLabel }}
                <span class="sort-icon">{{ sortCol === 'key' ? (sortDir > 0 ? '↑' : '↓') : '' }}</span>
              </th>
              <th v-for="col in cols" :key="col.k" @click="sortBy(col.k)" class="sortable">
                {{ col.l }}
                <span class="sort-icon">{{ sortCol === col.k ? (sortDir > 0 ? '↑' : '↓') : '' }}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in visibleRows" :key="row.key">
              <td style="font-weight:500;color:#d0d0d0;white-space:nowrap">{{ row.key }}</td>
              <td class="tabnum">{{ fmt(row.impressions) }}</td>
              <td class="tabnum">{{ fmt(row.clicks) }}</td>
              <td class="tabnum" :class="row.ctr > 1 ? 'val-positive' : ''">{{ row.ctr?.toFixed(2) }}%</td>
              <td class="tabnum">{{ fmt(row.conversions) }}</td>
              <td class="tabnum" :class="row.cr > 2 ? 'val-positive' : ''">{{ row.cr?.toFixed(2) }}%</td>
              <td class="tabnum">${{ fmtNum(row.spend) }}</td>
              <td class="tabnum val-positive">${{ fmtNum(row.revenue) }}</td>
              <td class="tabnum" :class="row.roi >= 0 ? 'val-positive' : 'val-negative'">
                {{ row.roi >= 0 ? '+' : '' }}{{ row.roi?.toFixed(1) }}%
              </td>
              <td class="tabnum val-accent">{{ row.win_rate?.toFixed(1) }}%</td>
              <td class="tabnum">${{ fmtNum(row.cpa) }}</td>
              <td class="tabnum">${{ fmtNum(row.cpm) }}</td>
            </tr>

            <!-- Totals row -->
            <tr class="totals-row">
              <td style="font-weight:700;color:#e0e0e0">Total</td>
              <td class="tabnum">{{ fmt(totals.impressions) }}</td>
              <td class="tabnum">{{ fmt(totals.clicks) }}</td>
              <td class="tabnum">{{ totals.ctr?.toFixed(2) }}%</td>
              <td class="tabnum">{{ fmt(totals.conversions) }}</td>
              <td class="tabnum">{{ totals.cr?.toFixed(2) }}%</td>
              <td class="tabnum">${{ fmtNum(totals.spend) }}</td>
              <td class="tabnum val-positive">${{ fmtNum(totals.revenue) }}</td>
              <td class="tabnum" :class="totals.roi >= 0 ? 'val-positive' : 'val-negative'">
                {{ totals.roi >= 0 ? '+' : '' }}{{ totals.roi?.toFixed(1) }}%
              </td>
              <td class="tabnum val-accent">{{ totals.win_rate?.toFixed(1) }}%</td>
              <td class="tabnum">${{ fmtNum(totals.cpa) }}</td>
              <td class="tabnum">${{ fmtNum(totals.cpm) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'

const props = defineProps({ period: String })

const loading  = ref(true)
const rows     = ref([])
const groupBy  = ref('date')
const search   = ref('')
const sortCol  = ref('')
const sortDir  = ref(-1)

const dimensions = [
  { v: 'date',     l: 'Date' },
  { v: 'campaign', l: 'Campaign' },
  { v: 'country',  l: 'Country' },
  { v: 'device',   l: 'Device' },
  { v: 'os',       l: 'OS' },
  { v: 'site',     l: 'Site' },
]

const cols = [
  { k: 'impressions', l: 'Impr.' },
  { k: 'clicks',      l: 'Clicks' },
  { k: 'ctr',         l: 'CTR' },
  { k: 'conversions', l: 'Conv.' },
  { k: 'cr',          l: 'CR' },
  { k: 'spend',       l: 'Spend' },
  { k: 'revenue',     l: 'Revenue' },
  { k: 'roi',         l: 'ROI' },
  { k: 'win_rate',    l: 'Win%' },
  { k: 'cpa',         l: 'CPA' },
  { k: 'cpm',         l: 'CPM' },
]

const fmt    = n => Number(n || 0).toLocaleString('en-US')
const fmtNum = n => Number(n || 0).toFixed(2)

const groupByLabel = computed(() => {
  return dimensions.find(d => d.v === groupBy.value)?.l || groupBy.value
})

const sum = key => rows.value.reduce((a, r) => a + (Number(r[key]) || 0), 0)

const totals = computed(() => {
  const imp  = sum('impressions')
  const clk  = sum('clicks')
  const conv = sum('conversions')
  const sp   = sum('spend')
  const rev  = sum('revenue')
  return {
    impressions: imp,
    clicks:      clk,
    ctr:         imp ? (clk / imp) * 100 : 0,
    conversions: conv,
    cr:          clk ? (conv / clk) * 100 : 0,
    spend:       sp,
    revenue:     rev,
    roi:         sp ? ((rev - sp) / sp) * 100 : 0,
    win_rate:    rows.value.length ? sum('win_rate') / rows.value.length : 0,
    cpa:         conv ? sp / conv : 0,
    cpm:         imp  ? (sp / imp) * 1000 : 0,
  }
})

/* KPI strip — top 5 metrics with mock spark data until real API */
const kpis = computed(() => [
  {
    id: 'imp',   label: 'Impressions',
    value: fmt(totals.value.impressions),
    delta: '12.4%', up: true,
    spark: rows.value.map(r => r.impressions || 0),
  },
  {
    id: 'clk',   label: 'Clicks',
    value: fmt(totals.value.clicks),
    delta: '8.1%', up: true,
    spark: rows.value.map(r => r.clicks || 0),
  },
  {
    id: 'ctr',   label: 'Avg CTR',
    value: totals.value.ctr.toFixed(2) + '%',
    delta: '0.3%', up: false,
    spark: rows.value.map(r => r.ctr || 0),
  },
  {
    id: 'roi',   label: 'ROI',
    value: (totals.value.roi >= 0 ? '+' : '') + totals.value.roi.toFixed(1) + '%',
    delta: '18.2%', up: totals.value.roi >= 0,
    spark: rows.value.map(r => r.roi || 0),
  },
  {
    id: 'spend', label: 'Spend',
    value: '$' + fmtNum(totals.value.spend),
    delta: '5.7%', up: false,
    spark: rows.value.map(r => r.spend || 0),
  },
])

function sparkPoints(data) {
  if (!data.length) return ''
  const min = Math.min(...data)
  const max = Math.max(...data)
  const range = max - min || 1
  return data.map((v, i) => `${i * 8},${28 - ((v - min) / range) * 24}`).join(' ')
}

const visibleRows = computed(() => {
  let list = rows.value
  if (search.value) {
    const q = search.value.toLowerCase()
    list = list.filter(r => String(r.key).toLowerCase().includes(q))
  }
  if (sortCol.value) {
    list = [...list].sort((a, b) => {
      const av = a[sortCol.value], bv = b[sortCol.value]
      if (typeof av === 'number') return (av - bv) * sortDir.value
      return String(av).localeCompare(String(bv)) * sortDir.value
    })
  }
  return list
})

function sortBy(col) {
  if (sortCol.value === col) sortDir.value *= -1
  else { sortCol.value = col; sortDir.value = -1 }
}

async function load() {
  loading.value = true
  try {
    const r = await fetch(`/api/analytics?group_by=${groupBy.value}&period=${props.period || '7d'}`)
    if (r.ok) rows.value = await r.json()
    else rows.value = []
  } catch { rows.value = [] }
  finally { loading.value = false }
}

function exportCsv() {
  const headers = ['Key','Impressions','Clicks','CTR%','Conversions','CR%','Spend','Revenue','ROI%','Win%','CPA','CPM']
  const lines = [
    headers.join(','),
    ...visibleRows.value.map(r =>
      [r.key, r.impressions, r.clicks,
       r.ctr?.toFixed(2), r.conversions, r.cr?.toFixed(2),
       r.spend, r.revenue, r.roi?.toFixed(1),
       r.win_rate?.toFixed(1), r.cpa?.toFixed(2), r.cpm?.toFixed(2)].join(',')
    ),
    ['Total', totals.value.impressions, totals.value.clicks,
     totals.value.ctr.toFixed(2), totals.value.conversions,
     totals.value.cr.toFixed(2), totals.value.spend.toFixed(2),
     totals.value.revenue.toFixed(2), totals.value.roi.toFixed(1),
     totals.value.win_rate.toFixed(1), totals.value.cpa.toFixed(2),
     totals.value.cpm.toFixed(2)].join(','),
  ]
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `analytics-${groupBy.value}-${props.period || '7d'}.csv`
  a.click()
}

onMounted(load)
watch([() => props.period, groupBy], load)
</script>

<style scoped>
.sparkline {
  display:block;
  height:28px;
  margin-top:8px;
  opacity:0.7;
}
.sortable { cursor:pointer; user-select:none; white-space:nowrap; }
.sortable:hover { color:#a0a0a0; }
.sort-icon { margin-left:3px; font-size:10px; color:#555; }
.totals-row td {
  border-top:1px solid rgba(255,255,255,0.1);
  padding-top:10px;
  color:#c0c0c0;
}
</style>
