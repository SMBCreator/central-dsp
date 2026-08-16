<template>
  <div class="page-wrap">

    <!-- Header -->
    <div class="page-header">
      <div>
        <h1 class="page-title">Networks</h1>
        <p class="page-subtitle">SSP and Ad Exchange connections — traffic sources for the RTB bidder</p>
      </div>
      <button @click="showAdd = true" class="btn btn-primary">Add Network</button>
    </div>

    <!-- Global health strip -->
    <div class="health-strip">
      <div class="health-item">
        <div class="h-val">{{ connected }}</div>
        <div class="h-label">Connected</div>
      </div>
      <div class="health-div"/>
      <div class="health-item">
        <div class="h-val" style="color:#f87171">{{ errored }}</div>
        <div class="h-label">Errors</div>
      </div>
      <div class="health-div"/>
      <div class="health-item">
        <div class="h-val tabnum">{{ totalQps }}</div>
        <div class="h-label">Bid req/s</div>
      </div>
      <div class="health-div"/>
      <div class="health-item">
        <div class="h-val tabnum">{{ avgLatency }}ms</div>
        <div class="h-label">Avg latency</div>
      </div>
      <div class="health-div"/>
      <div class="health-item">
        <div class="h-val tabnum" style="color:#34d399">${{ totalSpend }}</div>
        <div class="h-label">Spend today</div>
      </div>
    </div>

    <!-- Tabs -->
    <div class="tab-bar">
      <button v-for="t in tabs" :key="t.v"
        @click="activeTab = t.v"
        :class="['tab-btn', activeTab === t.v ? 'active' : '']">
        {{ t.l }}
      </button>
    </div>

    <!-- TAB: CONNECTIONS -->
    <div v-if="activeTab === 'connections'">
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:10px">
        <div v-for="net in networks" :key="net.id" class="net-card">
          <!-- Card header -->
          <div class="net-card-header">
            <div style="display:flex;align-items:center;gap:10px">
              <div class="net-logo" :style="{background: net.color + '14'}">
                <span :style="{color: net.color, fontSize:'11px', fontWeight:700}">{{ net.abbr }}</span>
              </div>
              <div>
                <div class="net-name">{{ net.name }}</div>
                <div class="net-type">{{ net.type }}</div>
              </div>
            </div>
            <div style="display:flex;align-items:center;gap:8px">
              <div :class="`status-dot ${net.status}`"/>
              <button @click="toggleNet(net)"
                :class="['toggle-btn', net.active ? 'on' : 'off']">
                <span class="toggle-knob"/>
              </button>
            </div>
          </div>

          <!-- Metrics row -->
          <div class="net-metrics">
            <div class="net-metric">
              <div class="nm-val tabnum">{{ net.qps }}</div>
              <div class="nm-label">req/s</div>
            </div>
            <div class="nm-div"/>
            <div class="net-metric">
              <div class="nm-val tabnum">{{ net.win_rate }}%</div>
              <div class="nm-label">Win rate</div>
            </div>
            <div class="nm-div"/>
            <div class="net-metric">
              <div class="nm-val tabnum">{{ net.latency }}ms</div>
              <div class="nm-label" :class="net.latency > 80 ? 'warn' : ''">Latency</div>
            </div>
            <div class="nm-div"/>
            <div class="net-metric">
              <div class="nm-val tabnum">${{ net.spend_today }}</div>
              <div class="nm-label">Today</div>
            </div>
          </div>

          <!-- Endpoint -->
          <div class="net-endpoint">
            <code class="endpoint-code">{{ net.endpoint }}</code>
            <button @click="copy(net.endpoint)" class="icon-btn">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2"/>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
              </svg>
            </button>
          </div>

          <!-- Formats -->
          <div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:8px">
            <span v-for="f in net.formats" :key="f" class="format-tag">{{ f }}</span>
          </div>

          <!-- Footer actions -->
          <div class="net-footer">
            <button @click="testConnection(net)" class="btn btn-ghost btn-xs"
              :disabled="net.testing">
              {{ net.testing ? 'Testing...' : 'Test' }}
            </button>
            <button @click="openEdit(net)" class="btn btn-ghost btn-xs">Settings</button>
            <span v-if="net.last_error" class="net-error">{{ net.last_error }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB: TRAFFIC BREAKDOWN -->
    <div v-if="activeTab === 'traffic'">
      <div style="display:flex;flex-direction:column;gap:10px">
        <!-- By network -->
        <div class="card">
          <div class="card-header-row">
            <span class="card-label">Traffic by Network</span>
            <span style="font-size:11px;color:#444">Last {{ props.period || '7d' }}</span>
          </div>
          <div class="overflow-x-auto">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Network</th>
                  <th>Bid Requests</th>
                  <th>Bids Sent</th>
                  <th>Fill Rate</th>
                  <th>Wins</th>
                  <th>Win Rate</th>
                  <th>Impressions</th>
                  <th>Clicks</th>
                  <th>Spend</th>
                  <th>Avg CPM</th>
                  <th>Avg Latency</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in trafficRows" :key="row.name">
                  <td style="font-weight:500;color:#d0d0d0">
                    <div style="display:flex;align-items:center;gap:6px">
                      <div class="dot" :style="{background: row.color}"/>
                      {{ row.name }}
                    </div>
                  </td>
                  <td class="tabnum">{{ fmt(row.bid_reqs) }}</td>
                  <td class="tabnum">{{ fmt(row.bids_sent) }}</td>
                  <td class="tabnum" :class="row.fill_rate > 50 ? 'val-positive' : ''">
                    {{ row.fill_rate }}%
                  </td>
                  <td class="tabnum">{{ fmt(row.wins) }}</td>
                  <td class="tabnum val-accent">{{ row.win_rate }}%</td>
                  <td class="tabnum">{{ fmt(row.impressions) }}</td>
                  <td class="tabnum">{{ fmt(row.clicks) }}</td>
                  <td class="tabnum">${{ row.spend }}</td>
                  <td class="tabnum">${{ row.cpm }}</td>
                  <td class="tabnum" :class="row.latency > 80 ? 'val-negative' : ''">
                    {{ row.latency }}ms
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Share bars -->
        <div class="card">
          <div class="card-header-row"><span class="card-label">Spend Share by Network</span></div>
          <div style="padding:16px;display:flex;flex-direction:column;gap:10px">
            <div v-for="row in trafficRows" :key="row.name+'bar'"
              style="display:flex;align-items:center;gap:10px">
              <div style="width:90px;font-size:12px;color:#666;text-align:right">{{ row.name }}</div>
              <div style="flex:1;background:rgba(255,255,255,0.04);border-radius:3px;height:6px">
                <div :style="{width: row.spend_pct+'%', background: row.color, borderRadius:'3px', height:'6px',
                  transition:'width 600ms cubic-bezier(0.16,1,0.3,1)'}"/>
              </div>
              <div class="tabnum" style="font-size:12px;width:40px">{{ row.spend_pct }}%</div>
              <div class="tabnum" style="font-size:12px;color:#444;width:52px">${{ row.spend }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB: ENDPOINT LOG -->
    <div v-if="activeTab === 'log'">
      <div class="card">
        <div class="card-header-row">
          <span class="card-label">Bid Request / Response Log</span>
          <div style="display:flex;gap:8px">
            <div class="live-dot"/>
            <select v-model="logNetwork" class="select select-xs">
              <option value="">All networks</option>
              <option v-for="n in networks" :key="n.id" :value="n.id">{{ n.name }}</option>
            </select>
          </div>
        </div>
        <div class="overflow-x-auto">
          <table class="data-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Network</th>
                <th>Type</th>
                <th>Imp ID</th>
                <th>Bid Price</th>
                <th>Win Price</th>
                <th>Latency</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="entry in filteredLog" :key="entry.id">
                <td style="font-size:11px;color:#555">{{ relTime(entry.ts) }}</td>
                <td style="font-size:11px;color:#888">{{ entry.network }}</td>
                <td>
                  <span :class="`event-badge event-${entry.type}`">{{ entry.type }}</span>
                </td>
                <td><code class="code-tag">{{ entry.imp_id?.slice(0,10) }}...</code></td>
                <td class="tabnum">${{ entry.bid_price?.toFixed(3) || '—' }}</td>
                <td class="tabnum val-positive">${{ entry.win_price?.toFixed(3) || '—' }}</td>
                <td class="tabnum" :class="entry.latency > 80 ? 'val-negative' : ''">
                  {{ entry.latency }}ms
                </td>
                <td>
                  <span :class="entry.won ? 'badge badge-active' : 'badge badge-draft'" style="font-size:10px">
                    {{ entry.won ? 'won' : entry.result || 'loss' }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Add / Edit Network Modal -->
    <Transition name="modal">
      <div v-if="showAdd || editNet" class="modal-backdrop" @click.self="closeModal">
        <div class="modal-panel" style="max-width:540px">
          <div class="modal-header">
            <div class="modal-title">{{ editNet ? 'Network Settings' : 'Add Network' }}</div>
            <button @click="closeModal" class="btn btn-ghost btn-sm">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>
            </button>
          </div>
          <div class="modal-body">
            <div style="display:flex;flex-direction:column;gap:14px">
              <div class="form-row">
                <label class="form-label">Network name</label>
                <input v-model="netForm.name" class="input" placeholder="e.g. Smaato" />
              </div>
              <div class="form-row">
                <label class="form-label">Type</label>
                <div class="chip-group">
                  <button v-for="t in ['SSP','Exchange','DSP','Direct']" :key="t"
                    @click="netForm.type = t"
                    :class="['chip', netForm.type === t ? 'active' : '']">
                    {{ t }}
                  </button>
                </div>
              </div>
              <div class="form-row">
                <label class="form-label">OpenRTB Endpoint URL</label>
                <input v-model="netForm.endpoint" class="input" placeholder="https://rtb.network.com/bid" />
              </div>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
                <div class="form-row">
                  <label class="form-label">Auth token</label>
                  <input v-model="netForm.token" class="input" type="password" placeholder="Bearer token" />
                </div>
                <div class="form-row">
                  <label class="form-label">Timeout (ms)</label>
                  <input v-model="netForm.timeout" class="input" type="number" placeholder="100" />
                </div>
              </div>
              <div class="form-row">
                <label class="form-label">Ad formats</label>
                <div class="chip-group">
                  <button v-for="f in allFormats" :key="f"
                    @click="toggleFormat(f)"
                    :class="['chip', netForm.formats.includes(f) ? 'active' : '']">
                    {{ f }}
                  </button>
                </div>
              </div>
              <div class="form-row">
                <label class="form-label">QPS limit</label>
                <input v-model="netForm.qps_limit" class="input" type="number" placeholder="500" />
              </div>
              <div class="info-box">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
                <span>Bidder will respect QPS limit and timeout. Latency above 100ms may cause SSP to ignore bids.</span>
              </div>
            </div>
          </div>
          <div class="modal-footer">
            <button @click="closeModal" class="btn btn-secondary">Cancel</button>
            <button @click="saveNet"
              :disabled="savingNet || !netForm.name || !netForm.endpoint"
              class="btn btn-primary">
              {{ savingNet ? 'Saving...' : (editNet ? 'Save' : 'Add Network') }}
            </button>
          </div>
        </div>
      </div>
    </Transition>

  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'

const props = defineProps({ period: String })

const activeTab  = ref('connections')
const showAdd    = ref(false)
const editNet    = ref(null)
const savingNet  = ref(false)
const logNetwork = ref('')

const tabs = [
  { v: 'connections', l: 'Connections' },
  { v: 'traffic',     l: 'Traffic Breakdown' },
  { v: 'log',         l: 'Endpoint Log' },
]

const allFormats = ['Banner', 'Native', 'Video', 'Interstitial', 'Rewarded']

const emptyForm = () => ({
  name: '', type: 'SSP', endpoint: '',
  token: '', timeout: 100,
  formats: ['Banner'], qps_limit: 500,
})
const netForm = ref(emptyForm())

function toggleFormat(f) {
  const i = netForm.value.formats.indexOf(f)
  if (i >= 0) netForm.value.formats.splice(i, 1)
  else netForm.value.formats.push(f)
}

// --- Mock data (replaced by API when backend ready) ---
const networks = ref([
  {
    id: 1, name: 'Smaato', abbr: 'SM', type: 'SSP', color: '#60a5fa',
    status: 'ok', active: true,
    qps: 312, win_rate: 3.2, latency: 42, spend_today: '284.10',
    endpoint: 'https://prebid.smaato.net/oapi/bidder',
    formats: ['Banner','Native','Video'],
    last_error: null, testing: false,
  },
  {
    id: 2, name: 'Mintegral', abbr: 'MG', type: 'Exchange', color: '#a78bfa',
    status: 'ok', active: true,
    qps: 248, win_rate: 2.8, latency: 61, spend_today: '198.40',
    endpoint: 'https://bidding.mintegral.com/openrtb/bid',
    formats: ['Interstitial','Rewarded','Banner'],
    last_error: null, testing: false,
  },
  {
    id: 3, name: 'Epom', abbr: 'EP', type: 'SSP', color: '#34d399',
    status: 'warn', active: true,
    qps: 89, win_rate: 1.4, latency: 95, spend_today: '61.20',
    endpoint: 'https://am.epom.com/rtb/bid',
    formats: ['Banner','Native'],
    last_error: 'High latency detected (95ms)', testing: false,
  },
  {
    id: 4, name: 'Beeswax', abbr: 'BW', type: 'Exchange', color: '#fbbf24',
    status: 'ok', active: false,
    qps: 0, win_rate: 0, latency: 0, spend_today: '0.00',
    endpoint: 'https://bidder.beeswax.com/openrtb2/auction',
    formats: ['Banner','Video','Native'],
    last_error: null, testing: false,
  },
])

const trafficRows = ref([
  { name:'Smaato',    color:'#60a5fa', bid_reqs:142800, bids_sent:98400,  fill_rate:69, wins:3148, win_rate:3.2, impressions:3148, clicks:94,  spend:'284.10', cpm:'90.2',  latency:42, spend_pct:42 },
  { name:'Mintegral', color:'#a78bfa', bid_reqs:98200,  bids_sent:71000,  fill_rate:72, wins:2748, win_rate:2.8, impressions:2748, clicks:71,  spend:'198.40', cpm:'72.2',  latency:61, spend_pct:30 },
  { name:'Epom',      color:'#34d399', bid_reqs:41000,  bids_sent:28000,  fill_rate:68, wins:574,  win_rate:1.4, impressions:574,  clicks:12,  spend:'61.20',  cpm:'106.6', latency:95, spend_pct:9  },
  { name:'Beeswax',   color:'#fbbf24', bid_reqs:0,      bids_sent:0,      fill_rate:0,  wins:0,    win_rate:0,   impressions:0,    clicks:0,   spend:'0.00',   cpm:'0.00',  latency:0,  spend_pct:0  },
])

const endpointLog = ref([
  { id:1, ts: new Date(Date.now()-12000).toISOString(), network:'Smaato',    type:'bid',  imp_id:'abc123def456', bid_price:1.240, win_price:1.180, latency:38, won:true,  result:'won'  },
  { id:2, ts: new Date(Date.now()-24000).toISOString(), network:'Mintegral', type:'bid',  imp_id:'xyz789ghi012', bid_price:0.980, win_price:null,  latency:55, won:false, result:'loss' },
  { id:3, ts: new Date(Date.now()-38000).toISOString(), network:'Smaato',    type:'nobid',imp_id:'jkl345mno678', bid_price:null,  win_price:null,  latency:18, won:false, result:'noBid'},
  { id:4, ts: new Date(Date.now()-61000).toISOString(), network:'Epom',      type:'bid',  imp_id:'pqr901stu234', bid_price:2.100, win_price:null,  latency:92, won:false, result:'loss' },
  { id:5, ts: new Date(Date.now()-94000).toISOString(), network:'Mintegral', type:'bid',  imp_id:'vwx567yza890', bid_price:1.560, win_price:1.490, latency:61, won:true,  result:'won'  },
])

const filteredLog = computed(() =>
  logNetwork.value
    ? endpointLog.value.filter(e => e.network === networks.value.find(n=>n.id==logNetwork.value)?.name)
    : endpointLog.value
)

const connected   = computed(() => networks.value.filter(n => n.active && n.status !== 'error').length)
const errored     = computed(() => networks.value.filter(n => n.status === 'error').length)
const totalQps    = computed(() => networks.value.reduce((a,n) => a + (n.active ? n.qps : 0), 0))
const avgLatency  = computed(() => {
  const active = networks.value.filter(n => n.active && n.latency)
  return active.length ? Math.round(active.reduce((a,n)=>a+n.latency,0)/active.length) : 0
})
const totalSpend  = computed(() =>
  networks.value.reduce((a,n) => a + parseFloat(n.spend_today||0), 0).toFixed(2)
)

const fmt = n => Number(n||0).toLocaleString('en-US')

function relTime(ts) {
  const d = Date.now() - new Date(ts).getTime()
  if (d < 60000)   return Math.floor(d/1000) + 's ago'
  if (d < 3600000) return Math.floor(d/60000) + 'm ago'
  return Math.floor(d/3600000) + 'h ago'
}

function copy(text) { navigator.clipboard?.writeText(text) }

function openEdit(net) {
  editNet.value = net
  netForm.value = { ...net, formats: [...net.formats] }
}

function closeModal() {
  showAdd.value  = false
  editNet.value  = null
  netForm.value  = emptyForm()
}

async function toggleNet(net) {
  net.active = !net.active
  try {
    await fetch(`/api/networks/${net.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active: net.active }),
    })
  } catch {}
}

async function testConnection(net) {
  net.testing = true
  try {
    const r = await fetch(`/api/networks/${net.id}/test`, { method: 'POST' })
    const data = r.ok ? await r.json() : null
    net.latency    = data?.latency || net.latency
    net.status     = data?.ok ? 'ok' : 'warn'
    net.last_error = data?.ok ? null : (data?.error || 'Test failed')
  } catch {
    net.status     = 'error'
    net.last_error = 'Connection failed'
  } finally {
    net.testing = false
  }
}

async function saveNet() {
  savingNet.value = true
  try {
    const url    = editNet.value ? `/api/networks/${editNet.value.id}` : '/api/networks'
    const method = editNet.value ? 'PUT' : 'POST'
    const r = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(netForm.value),
    })
    if (r.ok) {
      const saved = await r.json()
      if (editNet.value) {
        Object.assign(editNet.value, saved)
      } else {
        networks.value.push({ ...saved, testing: false })
      }
      closeModal()
    }
  } catch {}
  finally { savingNet.value = false }
}

onMounted(() => {})
</script>

<style scoped>
.health-strip {
  display:flex;align-items:center;
  background:#0e0e0e;border:1px solid rgba(255,255,255,0.06);
  border-radius:10px;padding:14px 20px;gap:0;
}
.health-item { display:flex;flex-direction:column;gap:2px;padding:0 20px; }
.h-val  { font-size:20px;font-weight:700;color:#e0e0e0;font-variant-numeric:tabular-nums; }
.h-label{ font-size:11px;color:#3d3d3d; }
.health-div { width:1px;height:32px;background:rgba(255,255,255,0.06); }

.tab-bar { display:flex;gap:2px;border-bottom:1px solid rgba(255,255,255,0.06); }
.tab-btn {
  padding:8px 14px;font-size:12px;color:#555;
  border:none;background:none;cursor:pointer;
  border-bottom:2px solid transparent;margin-bottom:-1px;transition:all 150ms;
}
.tab-btn:hover { color:#999; }
.tab-btn.active { color:#e0e0e0;border-bottom-color:#e0e0e0; }

/* Network card */
.net-card {
  background:#0e0e0e;border:1px solid rgba(255,255,255,0.06);
  border-radius:12px;padding:16px;
  display:flex;flex-direction:column;gap:0;
  transition:border-color 180ms;
}
.net-card:hover { border-color:rgba(255,255,255,0.1); }
.net-card-header {
  display:flex;align-items:center;justify-content:space-between;
  margin-bottom:14px;
}
.net-logo {
  width:34px;height:34px;border-radius:8px;
  display:flex;align-items:center;justify-content:center;flex-shrink:0;
}
.net-name { font-size:13px;font-weight:600;color:#d0d0d0; }
.net-type { font-size:10px;color:#444;margin-top:1px; }

.status-dot {
  width:7px;height:7px;border-radius:50%;
}
.status-dot.ok   { background:#34d399;box-shadow:0 0 5px #34d399; }
.status-dot.warn { background:#fbbf24;box-shadow:0 0 5px #fbbf24; }
.status-dot.error{ background:#f87171;box-shadow:0 0 5px #f87171; }

.toggle-btn {
  width:32px;height:18px;border-radius:9px;
  display:flex;align-items:center;padding:2px;
  transition:background 200ms;cursor:pointer;border:none;
}
.toggle-btn.on  { background:#34d399;justify-content:flex-end; }
.toggle-btn.off { background:rgba(255,255,255,0.1);justify-content:flex-start; }
.toggle-knob {
  width:14px;height:14px;border-radius:50%;
  background:#fff;display:block;
}

.net-metrics {
  display:flex;align-items:center;
  background:rgba(255,255,255,0.02);border-radius:8px;
  padding:10px 0;margin-bottom:12px;
}
.net-metric { flex:1;display:flex;flex-direction:column;align-items:center;gap:2px; }
.nm-val   { font-size:14px;font-weight:600;color:#d0d0d0;font-variant-numeric:tabular-nums; }
.nm-label { font-size:10px;color:#444; }
.nm-label.warn { color:#fbbf24; }
.nm-div { width:1px;height:24px;background:rgba(255,255,255,0.05); }

.net-endpoint {
  display:flex;align-items:center;gap:6px;
  background:#0a0a0a;border:1px solid rgba(255,255,255,0.06);
  border-radius:6px;padding:7px 10px;
}
.endpoint-code {
  flex:1;font-size:10px;font-family:monospace;
  color:#555;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.icon-btn {
  padding:3px;border-radius:4px;color:#444;
  transition:color 150ms,background 150ms;flex-shrink:0;
}
.icon-btn:hover { color:#888;background:rgba(255,255,255,0.06); }

.format-tag {
  font-size:10px;padding:2px 7px;border-radius:4px;
  background:rgba(255,255,255,0.04);color:#555;
  border:1px solid rgba(255,255,255,0.06);
}
.net-footer {
  display:flex;align-items:center;gap:6px;margin-top:12px;
  padding-top:10px;border-top:1px solid rgba(255,255,255,0.05);
}
.net-error { font-size:10px;color:#f87171;flex:1;text-align:right; }

/* Traffic breakdown */
.dot { width:8px;height:8px;border-radius:50%;flex-shrink:0; }

.card-header-row {
  display:flex;align-items:center;justify-content:space-between;
  padding:14px 16px;border-bottom:1px solid rgba(255,255,255,0.05);
}
.card-label { font-size:12px;font-weight:600;color:#888; }

/* Log -->
.event-badge {
  font-size:10px;padding:2px 6px;border-radius:4px;font-weight:600;
}
.event-bid   { background:rgba(96,165,250,0.1);color:#60a5fa; }
.event-noBid { background:rgba(255,255,255,0.05);color:#555; }
.event-win   { background:rgba(52,211,153,0.1);color:#34d399; }

.live-dot {
  width:6px;height:6px;border-radius:50%;
  background:#34d399;box-shadow:0 0 6px #34d399;
  animation:pulse 2s infinite;
}
@keyframes pulse { 0%,100%{opacity:1}50%{opacity:0.4} }

.code-tag {
  background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.07);
  border-radius:4px;padding:2px 6px;font-size:10px;font-family:monospace;color:#888;
}
.select-xs {
  background:#111;border:1px solid rgba(255,255,255,0.08);
  border-radius:6px;padding:4px 8px;font-size:11px;color:#888;
}
.info-box {
  display:flex;align-items:flex-start;gap:8px;
  background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);
  border-radius:7px;padding:10px 12px;
  font-size:11px;color:#555;line-height:1.5;
}

/* Modal */
.modal-backdrop {
  position:fixed;inset:0;background:rgba(0,0,0,0.7);
  backdrop-filter:blur(4px);display:flex;
  align-items:center;justify-content:center;z-index:200;padding:24px;
}
.modal-panel {
  background:#111;border:1px solid rgba(255,255,255,0.09);
  border-radius:14px;width:100%;max-width:540px;max-height:90vh;
  display:flex;flex-direction:column;overflow:hidden;
  box-shadow:0 24px 80px rgba(0,0,0,0.8);
}
.modal-header {
  display:flex;align-items:center;justify-content:space-between;
  padding:20px 24px;border-bottom:1px solid rgba(255,255,255,0.06);
}
.modal-title { font-size:15px;font-weight:600;color:#e0e0e0; }
.modal-body  { flex:1;overflow-y:auto;padding:20px 24px; }
.modal-footer {
  padding:16px 24px;border-top:1px solid rgba(255,255,255,0.06);
  display:flex;align-items:center;justify-content:flex-end;gap:8px;
}
.modal-enter-active { transition:all 180ms cubic-bezier(0.16,1,0.3,1); }
.modal-leave-active { transition:all 120ms ease-in; }
.modal-enter-from { opacity:0;transform:scale(0.97) translateY(8px); }
.modal-leave-to   { opacity:0;transform:scale(0.98) translateY(4px); }
</style>
