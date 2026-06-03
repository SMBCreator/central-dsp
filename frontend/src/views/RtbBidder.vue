<template>
  <div class="space-y-6">

    <!-- Page header -->
    <div class="page-header">
      <div>
        <h1 class="page-title">RTB Bidder</h1>
        <p class="page-subtitle">OpenRTB 2.5 bidding engine configuration and monitoring</p>
      </div>
      <div class="flex items-center gap-2">
        <button @click="testBid" :disabled="testing" class="btn btn-secondary">
          {{ testing ? 'Sending...' : 'Test Bid Request' }}
        </button>
        <button @click="refreshStats" class="btn btn-primary">Refresh</button>
      </div>
    </div>

    <!-- Status cards -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
      <div class="kpi-card">
        <div class="kpi-label">Bidder Status</div>
        <div class="flex items-center gap-2 mt-1">
          <span :class="['status-dot', status.online ? 'online' : 'offline']"></span>
          <span class="text-base font-semibold" :class="status.online ? 'text-positive' : 'text-negative'">
            {{ status.online ? 'Online' : 'Offline' }}
          </span>
        </div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Requests Today</div>
        <div class="kpi-value">{{ fmt(stats.requests) }}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Win Rate</div>
        <div class="kpi-value val-accent">{{ stats.winRate }}%</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Avg Response</div>
        <div class="kpi-value">{{ stats.avgResponseMs }}<span class="text-sm text-text-muted ml-1">ms</span></div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Bids Sent</div>
        <div class="kpi-value">{{ fmt(stats.bids) }}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Wins</div>
        <div class="kpi-value val-positive">{{ fmt(stats.wins) }}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Spend Today</div>
        <div class="kpi-value">${{ stats.spend }}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Avg CPM</div>
        <div class="kpi-value">${{ stats.cpm }}</div>
      </div>
    </div>

    <!-- Step-by-step campaign setup -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">New RTB Campaign</span>
        <span class="text-xs text-text-muted">Step {{ step }} of 4</span>
      </div>
      <div class="card-body">

        <!-- Steps indicator -->
        <div class="steps mb-8">
          <div v-for="(s, i) in steps" :key="i"
               :class="['step', i + 1 === step ? 'active' : i + 1 < step ? 'done' : '']">
            <div class="step-dot">{{ i + 1 < step ? '✓' : i + 1 }}</div>
            <span class="hidden sm:block">{{ s }}</span>
          </div>
          <div v-if="i < steps.length - 1" class="step-line" v-for="(_, i) in steps.slice(0,-1)" :key="'l'+i" />
        </div>

        <!-- Step 1: Campaign basics -->
        <div v-if="step === 1" class="space-y-5 max-w-lg">
          <h3 class="text-md font-semibold text-text-primary">Campaign Details</h3>

          <div>
            <label class="form-label">Campaign Name</label>
            <input v-model="form.name" class="input"
                   placeholder="e.g. US Mobile — Q3 2026" />
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div>
              <label class="form-label">Daily Budget ($)</label>
              <input v-model="form.dailyBudget" type="number" class="input" placeholder="100" />
            </div>
            <div>
              <label class="form-label">Total Budget ($)</label>
              <input v-model="form.totalBudget" type="number" class="input" placeholder="3000" />
            </div>
          </div>
          <div class="grid grid-cols-3 gap-4">
            <div>
              <label class="form-label">Bid Price (CPM)</label>
              <input v-model="form.bidPrice" type="number" step="0.01" class="input" placeholder="1.50" />
            </div>
            <div>
              <label class="form-label">Bid Floor</label>
              <input v-model="form.bidFloor" type="number" step="0.01" class="input" placeholder="0.01" />
            </div>
            <div>
              <label class="form-label">Bid Ceiling</label>
              <input v-model="form.bidCeiling" type="number" step="0.01" class="input" placeholder="5.00" />
            </div>
          </div>
          <div class="flex justify-end">
            <button @click="step = 2" :disabled="!form.name || !form.dailyBudget"
                    class="btn btn-primary">Next: Targeting</button>
          </div>
        </div>

        <!-- Step 2: Targeting -->
        <div v-if="step === 2" class="space-y-5 max-w-lg">
          <h3 class="text-md font-semibold text-text-primary">Targeting Rules</h3>

          <div>
            <label class="form-label">Countries (ISO codes, comma separated)</label>
            <input v-model="form.geoCountries" class="input"
                   placeholder="US, GB, DE, CA" />
            <p class="form-hint">Leave empty to target all countries</p>
          </div>

          <div>
            <label class="form-label">Device Types</label>
            <div class="flex flex-wrap gap-2 mt-1">
              <button v-for="d in deviceTypes" :key="d.v"
                @click="toggleDevice(d.v)"
                :class="[
                  'px-3 py-1.5 rounded-md text-sm border transition-all duration-150',
                  form.deviceTypes.includes(d.v)
                    ? 'border-brand bg-brand text-white'
                    : 'border-surface-border text-text-secondary hover:border-zinc-300'
                ]">
                {{ d.l }}
              </button>
            </div>
          </div>

          <div>
            <label class="form-label">Operating Systems</label>
            <div class="flex flex-wrap gap-2 mt-1">
              <button v-for="os in osList" :key="os"
                @click="toggleOs(os)"
                :class="[
                  'px-3 py-1.5 rounded-md text-sm border transition-all duration-150',
                  form.osList.includes(os)
                    ? 'border-brand bg-brand text-white'
                    : 'border-surface-border text-text-secondary hover:border-zinc-300'
                ]">
                {{ os }}
              </button>
            </div>
          </div>

          <div>
            <label class="form-label">Site Whitelist (domains)</label>
            <textarea v-model="form.siteWhitelist" class="textarea"
                      placeholder="example.com&#10;news.com" rows="3" />
            <p class="form-hint">One domain per line. Leave empty to allow all.</p>
          </div>

          <div class="flex justify-between">
            <button @click="step = 1" class="btn btn-secondary">Back</button>
            <button @click="step = 3" class="btn btn-primary">Next: Creative</button>
          </div>
        </div>

        <!-- Step 3: Creative -->
        <div v-if="step === 3" class="space-y-5 max-w-lg">
          <h3 class="text-md font-semibold text-text-primary">Creative</h3>

          <div>
            <label class="form-label">Format</label>
            <div class="flex gap-2">
              <button v-for="f in formats" :key="f"
                @click="form.format = f"
                :class="[
                  'px-4 py-2 rounded-md text-sm border font-medium transition-all duration-150',
                  form.format === f
                    ? 'border-brand bg-brand text-white'
                    : 'border-surface-border text-text-secondary hover:border-zinc-300'
                ]">
                {{ f.charAt(0).toUpperCase() + f.slice(1) }}
              </button>
            </div>
          </div>

          <!-- Banner fields -->
          <template v-if="form.format === 'banner'">
            <div class="grid grid-cols-2 gap-4">
              <div>
                <label class="form-label">Width (px)</label>
                <input v-model="form.width" type="number" class="input" placeholder="300" />
              </div>
              <div>
                <label class="form-label">Height (px)</label>
                <input v-model="form.height" type="number" class="input" placeholder="250" />
              </div>
            </div>
            <div>
              <label class="form-label">Image URL</label>
              <input v-model="form.imageUrl" class="input" placeholder="https://cdn.example.com/banner.png" />
            </div>
          </template>

          <!-- Native fields -->
          <template v-if="form.format === 'native'">
            <div>
              <label class="form-label">Title</label>
              <input v-model="form.title" class="input" placeholder="Your ad headline" />
            </div>
            <div>
              <label class="form-label">Description</label>
              <textarea v-model="form.description" class="textarea" placeholder="Short description..." rows="2" />
            </div>
            <div>
              <label class="form-label">Image URL</label>
              <input v-model="form.imageUrl" class="input" placeholder="https://cdn.example.com/image.png" />
            </div>
          </template>

          <!-- Video fields -->
          <template v-if="form.format === 'video'">
            <div>
              <label class="form-label">VAST URL</label>
              <input v-model="form.vastUrl" class="input" placeholder="https://example.com/vast.xml" />
            </div>
          </template>

          <div>
            <label class="form-label">Click URL</label>
            <input v-model="form.clickUrl" class="input" placeholder="https://example.com/landing" />
          </div>

          <div class="flex justify-between">
            <button @click="step = 2" class="btn btn-secondary">Back</button>
            <button @click="step = 4" :disabled="!form.clickUrl" class="btn btn-primary">Next: Review</button>
          </div>
        </div>

        <!-- Step 4: Review & Launch -->
        <div v-if="step === 4" class="space-y-5 max-w-lg">
          <h3 class="text-md font-semibold text-text-primary">Review & Launch</h3>

          <div class="rounded-lg border border-surface-border divide-y divide-surface-divider">
            <div class="px-4 py-3 flex justify-between text-sm">
              <span class="text-text-muted">Campaign name</span>
              <span class="font-medium">{{ form.name }}</span>
            </div>
            <div class="px-4 py-3 flex justify-between text-sm">
              <span class="text-text-muted">Daily budget</span>
              <span class="font-medium tabnum">${{ form.dailyBudget }}</span>
            </div>
            <div class="px-4 py-3 flex justify-between text-sm">
              <span class="text-text-muted">Bid price (CPM)</span>
              <span class="font-medium tabnum">${{ form.bidPrice }}</span>
            </div>
            <div class="px-4 py-3 flex justify-between text-sm">
              <span class="text-text-muted">Targeting</span>
              <span class="font-medium">
                {{ form.geoCountries || 'All countries' }} /
                {{ form.deviceTypes.length ? form.deviceTypes.join(', ') : 'All devices' }}
              </span>
            </div>
            <div class="px-4 py-3 flex justify-between text-sm">
              <span class="text-text-muted">Creative format</span>
              <span class="font-medium capitalize">{{ form.format }}</span>
            </div>
          </div>

          <div v-if="launchError" class="rounded-md bg-red-50 border border-red-200 px-4 py-3 text-sm text-negative">
            {{ launchError }}
          </div>

          <div class="flex justify-between">
            <button @click="step = 3" class="btn btn-secondary">Back</button>
            <button @click="launch" :disabled="launching" class="btn btn-primary">
              {{ launching ? 'Launching...' : 'Launch Campaign' }}
            </button>
          </div>
        </div>

      </div>
    </div>

    <!-- Active RTB campaigns table -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">Active RTB Campaigns</span>
        <span class="badge-rtb">{{ activeCampaigns.length }} running</span>
      </div>
      <div v-if="!activeCampaigns.length" class="empty-state py-10">
        <div class="empty-title">No active RTB campaigns</div>
        <div class="empty-desc">Use the wizard above to launch your first RTB campaign</div>
      </div>
      <div v-else class="overflow-x-auto">
        <table class="data-table">
          <thead>
            <tr>
              <th>Campaign</th>
              <th>Bid CPM</th>
              <th>Daily Budget</th>
              <th>Spent Today</th>
              <th>Win Rate</th>
              <th>Impressions</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in activeCampaigns" :key="c.id">
              <td>
                <div class="font-medium">{{ c.name }}</div>
                <div class="text-xs text-text-muted">{{ c.format }} · {{ c.targeting_summary }}</div>
              </td>
              <td class="tabnum">${{ c.bid_price }}</td>
              <td>
                <div class="tabnum text-sm">${{ c.daily_spent }} / ${{ c.daily_budget }}</div>
                <div class="progress-bar mt-1">
                  <div class="progress-fill bg-accent"
                       :style="{ width: Math.min(100, (c.daily_spent/c.daily_budget)*100) + '%' }" />
                </div>
              </td>
              <td class="tabnum">${{ c.daily_spent }}</td>
              <td class="tabnum val-accent">{{ c.win_rate }}%</td>
              <td class="tabnum">{{ fmt(c.impressions) }}</td>
              <td><span :class="`badge-${c.status}`">{{ c.status }}</span></td>
              <td>
                <div class="flex items-center gap-1">
                  <button @click="pauseCampaign(c.id)"
                          class="btn btn-ghost btn-sm">Pause</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Test bid result -->
    <div v-if="bidResult" class="card">
      <div class="card-header">
        <span class="card-title">Bid Request Test Result</span>
        <button @click="bidResult = null" class="btn btn-ghost btn-sm">Close</button>
      </div>
      <div class="card-body">
        <pre class="text-xs font-mono text-text-primary bg-surface-hover rounded-md p-4 overflow-auto">{{ JSON.stringify(bidResult, null, 2) }}</pre>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'

const props = defineProps({ period: String })

// ── Status & stats ───────────────────────────────────────
const status = ref({ online: false })
const stats  = ref({
  requests: 0, bids: 0, wins: 0,
  winRate: '0.0', spend: '0.00',
  cpm: '0.00', avgResponseMs: 0,
})
const activeCampaigns = ref([])
const testing   = ref(false)
const bidResult = ref(null)

const fmt = (n) => Number(n).toLocaleString('en-US')

// ── Wizard state ─────────────────────────────────────────
const step     = ref(1)
const launching = ref(false)
const launchError = ref('')
const steps = ['Details', 'Targeting', 'Creative', 'Review']

const deviceTypes = [
  { v: 2, l: 'Mobile' },
  { v: 1, l: 'Desktop' },
  { v: 3, l: 'Tablet' },
  { v: 4, l: 'CTV' },
]
const osList = ['android', 'ios', 'windows', 'macos', 'linux']
const formats = ['banner', 'native', 'video']

const form = ref({
  name: '', dailyBudget: '', totalBudget: '',
  bidPrice: '1.00', bidFloor: '0.01', bidCeiling: '5.00',
  geoCountries: '', deviceTypes: [], osList: [],
  siteWhitelist: '',
  format: 'banner', width: 300, height: 250,
  imageUrl: '', title: '', description: '',
  vastUrl: '', clickUrl: '',
})

const toggleDevice = (v) => {
  const i = form.value.deviceTypes.indexOf(v)
  i === -1 ? form.value.deviceTypes.push(v) : form.value.deviceTypes.splice(i, 1)
}
const toggleOs = (os) => {
  const i = form.value.osList.indexOf(os)
  i === -1 ? form.value.osList.push(os) : form.value.osList.splice(i, 1)
}

// ── Launch ───────────────────────────────────────────────
async function launch() {
  launching.value = true
  launchError.value = ''
  try {
    const payload = {
      name:         form.value.name,
      daily_budget: parseFloat(form.value.dailyBudget),
      total_budget: parseFloat(form.value.totalBudget) || null,
      bid_price:    parseFloat(form.value.bidPrice),
      bid_floor:    parseFloat(form.value.bidFloor),
      bid_ceiling:  parseFloat(form.value.bidCeiling),
      geo_countries: form.value.geoCountries
        ? form.value.geoCountries.split(',').map(s => s.trim().toUpperCase())
        : [],
      device_types: form.value.deviceTypes,
      os_list:      form.value.osList,
      site_whitelist: form.value.siteWhitelist
        ? form.value.siteWhitelist.split('\n').map(s => s.trim()).filter(Boolean)
        : [],
      creatives: [{
        format:      form.value.format,
        width:       form.value.width,
        height:      form.value.height,
        image_url:   form.value.imageUrl,
        title:       form.value.title,
        description: form.value.description,
        vast_url:    form.value.vastUrl,
        click_url:   form.value.clickUrl,
      }],
    }
    const res = await fetch('/api/rtb/campaigns', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    })
    if (!res.ok) throw new Error((await res.json()).detail || 'Launch failed')
    step.value = 1
    Object.assign(form.value, {
      name: '', dailyBudget: '', totalBudget: '',
      bidPrice: '1.00', geoCountries: '', deviceTypes: [], osList: [],
      siteWhitelist: '', imageUrl: '', clickUrl: '',
    })
    await loadStats()
  } catch (e) {
    launchError.value = e.message
  } finally {
    launching.value = false
  }
}

// ── Pause campaign ───────────────────────────────────────
async function pauseCampaign(id) {
  await fetch(`/api/campaigns/${id}`, {
    method:  'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ status: 'paused' }),
  })
  await loadStats()
}

// ── Test bid request ─────────────────────────────────────
async function testBid() {
  testing.value = true
  try {
    const res = await fetch('/bidder/ortb', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: 'test-' + Date.now(),
        imp: [{ id: '1', banner: { w: 300, h: 250 }, bidfloor: 0.01 }],
        site: { domain: 'example.com' },
        device: { ip: '8.8.8.8', devicetype: 2, os: 'android' },
      }),
    })
    bidResult.value = res.status === 204
      ? { status: 'no_bid', message: 'No eligible campaigns for this request' }
      : await res.json()
  } catch (e) {
    bidResult.value = { error: e.message }
  } finally {
    testing.value = false
  }
}

// ── Load stats ───────────────────────────────────────────
async function loadStats() {
  try {
    const [statusRes, statsRes, campRes] = await Promise.allSettled([
      fetch('/api/rtb/status'),
      fetch(`/api/rtb/stats/summary?period=${props.period || '7d'}`),
      fetch('/api/rtb/campaigns/active'),
    ])
    if (statusRes.status === 'fulfilled' && statusRes.value.ok) {
      const s = await statusRes.value.json()
      status.value = { online: s.status === 'UP', ...s }
    }
    if (statsRes.status === 'fulfilled' && statsRes.value.ok) {
      Object.assign(stats.value, await statsRes.value.json())
    }
    if (campRes.status === 'fulfilled' && campRes.value.ok) {
      activeCampaigns.value = await campRes.value.json()
    }
  } catch (e) {
    console.warn('RTB stats load error:', e)
  }
}

const refreshStats = () => loadStats()
onMounted(loadStats)
watch(() => props.period, loadStats)
</script>
