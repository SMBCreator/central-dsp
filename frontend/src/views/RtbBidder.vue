<template>
  <div class="page-wrap">

    <!-- Page header -->
    <div class="page-header">
      <div>
        <h1 class="page-title">RTB Bidder</h1>
        <p class="page-subtitle">OpenRTB 2.5 engine — monitoring and campaign setup</p>
      </div>
      <div style="display:flex;gap:8px">
        <button @click="testBid" :disabled="testing" class="btn btn-secondary">
          {{ testing ? 'Sending...' : 'Test Bid' }}
        </button>
        <button @click="loadStats" class="btn btn-secondary">Refresh</button>
        <button @click="showWizard = true" class="btn btn-primary">New Campaign</button>
      </div>
    </div>

    <!-- KPI strip -->
    <div class="kpi-grid">
      <div class="kpi-cell">
        <div class="kpi-label">Status</div>
        <div class="flex items-center gap-2 mt-1">
          <span :class="['status-dot', status.online ? 'online' : 'offline']" />
          <span style="font-size:15px;font-weight:600" :style="status.online ? 'color:#4ade80' : 'color:#555'">
            {{ status.online ? 'Online' : 'Offline' }}
          </span>
        </div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Requests Today</div>
        <div class="kpi-value">{{ fmt(stats.requests) }}</div>
        <div class="kpi-delta flat">bid requests</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Bids Sent</div>
        <div class="kpi-value">{{ fmt(stats.bids) }}</div>
        <div class="kpi-delta flat">{{ bidRatio }}% ratio</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Wins</div>
        <div class="kpi-value val-positive">{{ fmt(stats.wins) }}</div>
        <div class="kpi-delta flat">Won auctions</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Win Rate</div>
        <div class="kpi-value val-accent">{{ stats.winRate }}%</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Avg Response</div>
        <div class="kpi-value">
          {{ stats.avgResponseMs }}<span style="font-size:12px;color:#555;margin-left:2px">ms</span>
        </div>
        <div :class="['kpi-delta', stats.avgResponseMs < 100 ? 'up' : 'down']">
          {{ stats.avgResponseMs < 100 ? 'Healthy' : 'Slow' }}
        </div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Spend Today</div>
        <div class="kpi-value">${{ stats.spend }}</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Avg CPM</div>
        <div class="kpi-value">${{ stats.cpm }}</div>
      </div>
    </div>

    <!-- Active RTB campaigns -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">Active RTB Campaigns</div>
        <span class="badge badge-rtb">{{ activeCampaigns.length }} running</span>
      </div>

      <div v-if="!activeCampaigns.length" class="empty-state">
        <div class="empty-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>
          </svg>
        </div>
        <div class="empty-title">No RTB campaigns running</div>
        <div class="empty-desc">Use New Campaign to launch your first RTB campaign</div>
        <button @click="showWizard = true" class="btn btn-secondary btn-sm" style="margin-top:16px">
          Launch campaign
        </button>
      </div>

      <div v-else class="overflow-x-auto">
        <table class="data-table">
          <thead>
            <tr>
              <th>Campaign</th>
              <th>Format</th>
              <th>Bid CPM</th>
              <th>Budget</th>
              <th>Spent</th>
              <th>Win Rate</th>
              <th>Impressions</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in activeCampaigns" :key="c.id">
              <td>
                <div style="font-weight:500;color:#d0d0d0">{{ c.name }}</div>
                <div style="font-size:11px;color:#444">{{ c.targeting_summary || 'All traffic' }}</div>
              </td>
              <td><span class="badge badge-draft" style="text-transform:uppercase;letter-spacing:0.05em">{{ c.format }}</span></td>
              <td class="tabnum">${{ c.bid_price }}</td>
              <td>
                <div class="tabnum" style="font-size:12px">${{ c.daily_spent }} / ${{ c.daily_budget }}</div>
                <div class="progress-track" style="margin-top:5px;width:80px">
                  <div class="progress-fill"
                    :class="budgetClass(c)"
                    :style="{ width: Math.min(100,(c.daily_spent/c.daily_budget)*100)+'%' }" />
                </div>
              </td>
              <td class="tabnum">${{ c.daily_spent }}</td>
              <td class="tabnum val-accent">{{ c.win_rate }}%</td>
              <td class="tabnum">{{ fmt(c.impressions) }}</td>
              <td><span :class="`badge badge-${c.status}`">{{ c.status }}</span></td>
              <td>
                <button @click="pauseCampaign(c.id)" class="btn btn-ghost btn-xs">Pause</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Test result -->
    <div v-if="bidResult" class="card">
      <div class="card-header">
        <div class="card-title">Bid Test Result</div>
        <button @click="bidResult = null" class="btn btn-ghost btn-sm">Close</button>
      </div>
      <div class="card-body">
        <pre class="code-block">{{ JSON.stringify(bidResult, null, 2) }}</pre>
      </div>
    </div>

    <!-- WIZARD MODAL -->
    <Transition name="modal">
      <div v-if="showWizard" class="modal-backdrop" @click.self="closeWizard">
        <div class="modal-panel">

          <div class="modal-header">
            <div>
              <div class="modal-title">New RTB Campaign</div>
              <div class="modal-subtitle">Step {{ step }} of {{ steps.length }}</div>
            </div>
            <button @click="closeWizard" class="btn btn-ghost btn-sm">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M18 6 6 18M6 6l12 12"/>
              </svg>
            </button>
          </div>

          <div class="modal-steps">
            <template v-for="(s, i) in steps" :key="i">
              <div :class="['step-item', step === i+1 ? 'active' : step > i+1 ? 'done' : '']">
                <div class="step-num">
                  <svg v-if="step > i+1" width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2.5">
                    <polyline points="2,6 5,9 10,3"/>
                  </svg>
                  <span v-else>{{ i + 1 }}</span>
                </div>
                <span class="step-label">{{ s }}</span>
              </div>
              <div v-if="i < steps.length - 1" :class="['step-connector', step > i+1 ? 'done' : '']" />
            </template>
          </div>

          <div class="modal-body">

            <!-- Step 1: Details -->
            <div v-if="step === 1" class="step-content">
              <div class="step-section-title">Campaign details</div>
              <div class="form-row">
                <label class="form-label">Campaign name</label>
                <input v-model="form.name" class="input" placeholder="e.g. US Mobile Q3 2026" />
              </div>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
                <div class="form-row">
                  <label class="form-label">Daily budget ($)</label>
                  <input v-model="form.dailyBudget" type="number" class="input" placeholder="100" />
                </div>
                <div class="form-row">
                  <label class="form-label">Total budget ($)</label>
                  <input v-model="form.totalBudget" type="number" class="input" placeholder="3000" />
                </div>
              </div>
              <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px">
                <div class="form-row">
                  <label class="form-label">Bid CPM ($)</label>
                  <input v-model="form.bidPrice" type="number" step="0.01" class="input" placeholder="1.50" />
                </div>
                <div class="form-row">
                  <label class="form-label">Floor ($)</label>
                  <input v-model="form.bidFloor" type="number" step="0.01" class="input" placeholder="0.01" />
                </div>
                <div class="form-row">
                  <label class="form-label">Ceiling ($)</label>
                  <input v-model="form.bidCeiling" type="number" step="0.01" class="input" placeholder="5.00" />
                </div>
              </div>
              <div class="bid-estimate" v-if="form.dailyBudget && form.bidPrice">
                <div class="estimate-row">
                  <span>Est. daily impressions</span>
                  <span>~{{ estImpressions }}</span>
                </div>
                <div class="estimate-row">
                  <span>Est. monthly spend</span>
                  <span>${{ estMonthlySpend }}</span>
                </div>
              </div>
            </div>

            <!-- Step 2: Targeting -->
            <div v-if="step === 2" class="step-content">
              <div class="step-section-title">Targeting rules</div>
              <div class="form-row">
                <label class="form-label">Countries <span class="val-muted">(ISO-2, comma separated)</span></label>
                <input v-model="form.geoCountries" class="input" placeholder="US, GB, DE, CA — leave empty for all" />
              </div>
              <div class="form-row">
                <label class="form-label">Device types</label>
                <div class="chip-group">
                  <button v-for="d in deviceTypes" :key="d.v"
                    @click="toggleDevice(d.v)"
                    :class="['chip', form.deviceTypes.includes(d.v) ? 'active' : '']">
                    {{ d.l }}
                  </button>
                </div>
              </div>
              <div class="form-row">
                <label class="form-label">Operating systems</label>
                <div class="chip-group">
                  <button v-for="os in osList" :key="os"
                    @click="toggleOs(os)"
                    :class="['chip', form.osList.includes(os) ? 'active' : '']">
                    {{ os }}
                  </button>
                </div>
              </div>
              <div class="form-row">
                <label class="form-label">Site whitelist</label>
                <textarea v-model="form.siteWhitelist" class="textarea" rows="3"
                  placeholder="example.com&#10;news.com&#10;Leave empty to allow all domains" />
              </div>
              <div class="form-row">
                <label class="form-label">Site blacklist</label>
                <textarea v-model="form.siteBlacklist" class="textarea" rows="2"
                  placeholder="blockedsite.com" />
              </div>
            </div>

            <!-- Step 3: Creative -->
            <div v-if="step === 3" class="step-content">
              <div class="step-section-title">Creative</div>
              <div class="form-row">
                <label class="form-label">Format</label>
                <div class="chip-group">
                  <button v-for="f in formats" :key="f"
                    @click="form.format = f"
                    :class="['chip', form.format === f ? 'active' : '']">
                    {{ f }}
                  </button>
                </div>
              </div>
              <template v-if="form.format === 'banner'">
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
                  <div class="form-row">
                    <label class="form-label">Width (px)</label>
                    <input v-model="form.width" type="number" class="input" placeholder="300" />
                  </div>
                  <div class="form-row">
                    <label class="form-label">Height (px)</label>
                    <input v-model="form.height" type="number" class="input" placeholder="250" />
                  </div>
                </div>
                <div class="form-row">
                  <label class="form-label">Image URL</label>
                  <input v-model="form.imageUrl" class="input" placeholder="https://cdn.example.com/banner.png" />
                </div>
              </template>
              <template v-if="form.format === 'native'">
                <div class="form-row">
                  <label class="form-label">Title</label>
                  <input v-model="form.title" class="input" placeholder="Your ad headline" />
                </div>
                <div class="form-row">
                  <label class="form-label">Description</label>
                  <textarea v-model="form.description" class="textarea" rows="2"
                    placeholder="Short ad description..." />
                </div>
                <div class="form-row">
                  <label class="form-label">Image URL</label>
                  <input v-model="form.imageUrl" class="input" placeholder="https://cdn.example.com/image.png" />
                </div>
              </template>
              <template v-if="form.format === 'video'">
                <div class="form-row">
                  <label class="form-label">VAST URL</label>
                  <input v-model="form.vastUrl" class="input" placeholder="https://example.com/vast.xml" />
                </div>
              </template>
              <div class="form-row">
                <label class="form-label">Click URL</label>
                <input v-model="form.clickUrl" class="input" placeholder="https://example.com/landing" />
              </div>
            </div>

            <!-- Step 4: Review -->
            <div v-if="step === 4" class="step-content">
              <div class="step-section-title">Review & launch</div>
              <div class="review-card">
                <div class="meta-row"><span class="meta-key">Campaign name</span><span class="meta-value">{{ form.name }}</span></div>
                <div class="meta-row"><span class="meta-key">Daily budget</span><span class="meta-value">${{ form.dailyBudget }}</span></div>
                <div class="meta-row"><span class="meta-key">Total budget</span><span class="meta-value">{{ form.totalBudget ? '$'+form.totalBudget : 'Unlimited' }}</span></div>
                <div class="meta-row"><span class="meta-key">Bid CPM</span><span class="meta-value">${{ form.bidPrice }}</span></div>
                <div class="meta-row"><span class="meta-key">Bid floor / ceiling</span><span class="meta-value">${{ form.bidFloor }} — ${{ form.bidCeiling }}</span></div>
                <div class="meta-row"><span class="meta-key">Countries</span><span class="meta-value">{{ form.geoCountries || 'All countries' }}</span></div>
                <div class="meta-row">
                  <span class="meta-key">Devices</span>
                  <span class="meta-value">{{ form.deviceTypes.length ? form.deviceTypes.map(v => deviceTypes.find(d=>d.v===v)?.l).join(', ') : 'All devices' }}</span>
                </div>
                <div class="meta-row"><span class="meta-key">OS</span><span class="meta-value">{{ form.osList.length ? form.osList.join(', ') : 'All' }}</span></div>
                <div class="meta-row"><span class="meta-key">Creative format</span><span class="meta-value" style="text-transform:capitalize">{{ form.format }}</span></div>
                <div class="meta-row"><span class="meta-key">Click URL</span><span class="meta-value" style="font-size:12px;word-break:break-all">{{ form.clickUrl || '—' }}</span></div>
              </div>
              <div v-if="launchError" class="error-alert">{{ launchError }}</div>
            </div>

          </div>

          <div class="modal-footer">
            <button v-if="step > 1" @click="step--" class="btn btn-secondary">Back</button>
            <span v-else />
            <div style="display:flex;gap:8px">
              <button v-if="step < steps.length" @click="nextStep" :disabled="!canProceed" class="btn btn-primary">Continue</button>
              <button v-else @click="launch" :disabled="launching" class="btn btn-primary">
                {{ launching ? 'Launching...' : 'Launch Campaign' }}
              </button>
            </div>
          </div>

        </div>
      </div>
    </Transition>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { apiFetch } from '../auth'

const props = defineProps({ period: String })

const status  = ref({ online: false })
const stats   = ref({
  requests:0, bids:0, wins:0,
  winRate:'0.0', spend:'0.00', cpm:'0.00', avgResponseMs:0,
})
const activeCampaigns = ref([])
const testing    = ref(false)
const bidResult  = ref(null)
const showWizard = ref(false)

const step        = ref(1)
const launching   = ref(false)
const launchError = ref('')
const steps = ['Details', 'Targeting', 'Creative', 'Review']

const deviceTypes = [
  { v:2, l:'Mobile' }, { v:1, l:'Desktop' }, { v:3, l:'Tablet' }, { v:4, l:'CTV' },
]
const osList  = ['Android', 'iOS', 'Windows', 'macOS', 'Linux']
const formats = ['banner', 'native', 'video']

const form = ref({
  name:'', dailyBudget:'', totalBudget:'',
  bidPrice:'1.00', bidFloor:'0.01', bidCeiling:'5.00',
  geoCountries:'', deviceTypes:[], osList:[],
  siteWhitelist:'', siteBlacklist:'',
  format:'banner', width:300, height:250,
  imageUrl:'', title:'', description:'', vastUrl:'', clickUrl:'',
})

const fmt      = n => Number(n).toLocaleString('en-US')
const bidRatio = computed(() => {
  if (!stats.value.requests) return '0.0'
  return ((stats.value.bids / stats.value.requests) * 100).toFixed(1)
})
const estImpressions = computed(() => {
  const b = parseFloat(form.value.dailyBudget)
  const c = parseFloat(form.value.bidPrice)
  if (!b || !c) return '—'
  return fmt(Math.round((b / c) * 1000))
})
const estMonthlySpend = computed(() => {
  const b = parseFloat(form.value.dailyBudget)
  if (!b) return '—'
  return (b * 30).toFixed(0)
})
const canProceed = computed(() => {
  if (step.value === 1) return form.value.name && form.value.dailyBudget && form.value.bidPrice
  if (step.value === 3) return !!form.value.clickUrl
  return true
})
const budgetClass = c => {
  const pct = (c.daily_spent / c.daily_budget) * 100
  return pct > 90 ? 'red' : pct > 70 ? 'amber' : 'green'
}

const toggleDevice = v => {
  const i = form.value.deviceTypes.indexOf(v)
  i === -1 ? form.value.deviceTypes.push(v) : form.value.deviceTypes.splice(i, 1)
}
const toggleOs = os => {
  const i = form.value.osList.indexOf(os)
  i === -1 ? form.value.osList.push(os) : form.value.osList.splice(i, 1)
}
const nextStep  = () => { if (canProceed.value) step.value++ }
const closeWizard = () => { showWizard.value = false; step.value = 1; launchError.value = '' }

async function launch() {
  launching.value = true
  launchError.value = ''
  try {
    // Step 1: create campaign
    const campRes = await apiFetch('/api/campaigns', {
      method: 'POST',
      body: {
        name:         form.value.name,
        daily_budget: parseFloat(form.value.dailyBudget),
        total_budget: parseFloat(form.value.totalBudget) || null,
        bid_price:    parseFloat(form.value.bidPrice),
        status:       'active',
        type:         'rtb',
      },
    })
    if (!campRes.ok) throw new Error((await campRes.json()).detail || 'Failed to create campaign')
    const campaign = await campRes.json()

    // Step 2: set targeting
    const targRes = await apiFetch(`/api/rtb/campaigns/${campaign.id}/targeting`, {
      method: 'PUT',
      body: {
        bid_floor:      parseFloat(form.value.bidFloor),
        bid_ceiling:    parseFloat(form.value.bidCeiling),
        geo_countries:  form.value.geoCountries
          ? form.value.geoCountries.split(',').map(s => s.trim().toUpperCase())
          : [],
        device_types:   form.value.deviceTypes,
        os_list:        form.value.osList,
        site_whitelist: form.value.siteWhitelist
          ? form.value.siteWhitelist.split('\n').map(s => s.trim()).filter(Boolean)
          : [],
        site_blacklist: form.value.siteBlacklist
          ? form.value.siteBlacklist.split('\n').map(s => s.trim()).filter(Boolean)
          : [],
      },
    })
    if (!targRes.ok) throw new Error((await targRes.json()).detail || 'Failed to set targeting')

    // Step 3: add creative
    const creativeRes = await apiFetch(`/api/rtb/campaigns/${campaign.id}/creatives`, {
      method: 'POST',
      body: {
        format:      form.value.format,
        width:       form.value.format === 'banner' ? parseInt(form.value.width)  : null,
        height:      form.value.format === 'banner' ? parseInt(form.value.height) : null,
        image_url:   form.value.imageUrl    || null,
        title:       form.value.title       || null,
        description: form.value.description || null,
        vast_url:    form.value.vastUrl     || null,
        click_url:   form.value.clickUrl,
      },
    })
    if (!creativeRes.ok) throw new Error((await creativeRes.json()).detail || 'Failed to add creative')

    closeWizard()
    await loadStats()
  } catch (e) {
    launchError.value = e.message
  } finally {
    launching.value = false
  }
}

async function pauseCampaign(id) {
  await apiFetch(`/api/campaigns/${id}`, {
    method: 'PATCH',
    body: { status: 'paused' },
  })
  await loadStats()
}

async function testBid() {
  testing.value = true
  try {
    const res = await apiFetch('/bidder/ortb', {
      method: 'POST',
      body: {
        id:     'test-' + Date.now(),
        imp:    [{ id: '1', banner: { w: 300, h: 250 }, bidfloor: 0.01 }],
        site:   { domain: 'example.com' },
        device: { ip: '8.8.8.8', devicetype: 2, os: 'android' },
      },
    })
    bidResult.value = res.status === 204
      ? { status: 'no_bid', message: 'No eligible campaigns matched this request' }
      : await res.json()
  } catch (e) {
    bidResult.value = { error: e.message }
  } finally {
    testing.value = false
  }
}

async function loadStats() {
  try {
    const [statusRes, campRes] = await Promise.allSettled([
      apiFetch('/api/rtb/status'),
      apiFetch('/api/rtb/campaigns'),
    ])

    if (statusRes.status === 'fulfilled' && statusRes.value.ok) {
      const s = await statusRes.value.json()
      status.value = { online: s.status === 'UP', ...s }
      stats.value.requests      = s.requests_today  ?? stats.value.requests
      stats.value.bids          = s.bids_today      ?? stats.value.bids
      stats.value.wins          = s.wins_today      ?? stats.value.wins
      stats.value.winRate       = s.win_rate        ?? stats.value.winRate
      stats.value.spend         = s.spend_today     ?? stats.value.spend
      stats.value.cpm           = s.avg_cpm         ?? stats.value.cpm
      stats.value.avgResponseMs = s.avg_response_ms ?? stats.value.avgResponseMs
    }

    if (campRes.status === 'fulfilled' && campRes.value.ok) {
      activeCampaigns.value = await campRes.value.json()
    }
  } catch (e) { console.warn('loadStats error:', e) }
}

onMounted(loadStats)
watch(() => props.period, loadStats)
</script>

<style scoped>
.modal-backdrop {
  position:fixed;inset:0;background:rgba(0,0,0,0.7);
  backdrop-filter:blur(4px);display:flex;
  align-items:center;justify-content:center;z-index:200;padding:24px;
}
.modal-panel {
  background:#111;border:1px solid rgba(255,255,255,0.09);
  border-radius:14px;width:100%;max-width:560px;max-height:90vh;
  display:flex;flex-direction:column;overflow:hidden;
  box-shadow:0 24px 80px rgba(0,0,0,0.8);
}
.modal-header {
  display:flex;align-items:flex-start;justify-content:space-between;
  padding:20px 24px 0;flex-shrink:0;
}
.modal-title    { font-size:15px;font-weight:600;color:#e0e0e0; }
.modal-subtitle { font-size:11px;color:#444;margin-top:2px; }
.modal-steps    { display:flex;align-items:center;padding:20px 24px 0;flex-shrink:0; }
.modal-body     { flex:1;overflow-y:auto;padding:20px 24px; }
.modal-footer   { padding:16px 24px;border-top:1px solid rgba(255,255,255,0.06);display:flex;align-items:center;justify-content:space-between;flex-shrink:0; }
.step-content   { display:flex;flex-direction:column;gap:16px; }
.step-section-title { font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.07em;color:#3d3d3d;padding-bottom:12px;border-bottom:1px solid rgba(255,255,255,0.05);margin-bottom:4px; }
.chip-group     { display:flex;flex-wrap:wrap;gap:6px; }
.bid-estimate   { background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);border-radius:8px;padding:12px 14px; }
.estimate-row   { display:flex;justify-content:space-between;font-size:12px;color:#555;padding:3px 0; }
.estimate-row span:last-child { color:#888;font-variant-numeric:tabular-nums; }
.review-card    { background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);border-radius:8px;overflow:hidden; }
.review-card .meta-row { padding:10px 14px; }
.error-alert    { background:rgba(248,113,113,0.08);border:1px solid rgba(248,113,113,0.2);border-radius:7px;padding:10px 14px;font-size:12px;color:#f87171; }
.modal-enter-active { transition:all 180ms cubic-bezier(0.16,1,0.3,1); }
.modal-leave-active { transition:all 120ms ease-in; }
.modal-enter-from   { opacity:0;transform:scale(0.97) translateY(8px); }
.modal-leave-to     { opacity:0;transform:scale(0.98) translateY(4px); }
</style>
