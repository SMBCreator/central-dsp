<template>
  <div class="page-wrap">

    <!-- Header -->
    <div class="page-header">
      <div>
        <h1 class="page-title">Pixel Center</h1>
        <p class="page-subtitle">Tracking, attribution and conversion data feed into DSP optimization</p>
      </div>
      <button @click="showCreatePixel = true" class="btn btn-primary">New Pixel</button>
    </div>

    <!-- Attribution flow banner -->
    <div class="flow-banner">
      <div class="flow-step">
        <div class="flow-icon">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>
          </svg>
        </div>
        <div class="flow-label">Impression</div>
        <div class="flow-sub">RTB Win → pixel fire</div>
      </div>
      <div class="flow-arrow">→</div>
      <div class="flow-step">
        <div class="flow-icon">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/>
            <polyline points="10 17 15 12 10 7"/>
            <line x1="15" y1="12" x2="3" y2="12"/>
          </svg>
        </div>
        <div class="flow-label">Click</div>
        <div class="flow-sub">Click tracker URL</div>
      </div>
      <div class="flow-arrow">→</div>
      <div class="flow-step">
        <div class="flow-icon">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07"/>
            <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 3.15 13a19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 2.06 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L6.09 9.91"/>
          </svg>
        </div>
        <div class="flow-label">Postback / S2S</div>
        <div class="flow-sub">Conversion signal</div>
      </div>
      <div class="flow-arrow">→</div>
      <div class="flow-step active">
        <div class="flow-icon">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 20h9"/>
            <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
          </svg>
        </div>
        <div class="flow-label">DSP Optimizer</div>
        <div class="flow-sub">Bid adjustment + ML</div>
      </div>
    </div>

    <!-- Tabs -->
    <div class="tab-bar">
      <button v-for="t in tabs" :key="t.v"
        @click="activeTab = t.v"
        :class="['tab-btn', activeTab === t.v ? 'active' : '']">
        {{ t.l }}
        <span v-if="t.count !== undefined" class="tab-count">{{ t.count }}</span>
      </button>
    </div>

    <!-- ── TAB: PIXELS ── -->
    <div v-if="activeTab === 'pixels'">
      <div class="card">
        <div v-if="loadingPixels" style="padding:20px;display:flex;flex-direction:column;gap:8px">
          <div v-for="i in 4" :key="i" class="skeleton" style="height:48px" />
        </div>
        <div v-else-if="!pixels.length" class="empty-state">
          <div class="empty-icon">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
              <rect x="3" y="3" width="5" height="5"/><rect x="16" y="3" width="5" height="5"/>
              <rect x="3" y="16" width="5" height="5"/>
              <path d="M21 16h-3a2 2 0 0 0-2 2v3M21 21v.01M12 7v3a2 2 0 0 1-2 2H7"/>
            </svg>
          </div>
          <div class="empty-title">No pixels yet</div>
          <div class="empty-desc">Create a pixel to start tracking impressions and conversions</div>
          <button @click="showCreatePixel = true" class="btn btn-secondary btn-sm" style="margin-top:16px">Create Pixel</button>
        </div>
        <div v-else class="overflow-x-auto">
          <table class="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Pixel ID</th>
                <th>Fires (7d)</th>
                <th>Last Fire</th>
                <th>Campaign</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="px in pixels" :key="px.id">
                <td style="font-weight:500;color:#d0d0d0">{{ px.name }}</td>
                <td><span :class="`badge badge-${px.type}`">{{ px.type }}</span></td>
                <td>
                  <div style="display:flex;align-items:center;gap:6px">
                    <code class="code-tag">{{ px.pixel_id }}</code>
                    <button @click="copy(px.pixel_id)" class="icon-btn" title="Copy ID">
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <rect x="9" y="9" width="13" height="13" rx="2"/>
                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                      </svg>
                    </button>
                  </div>
                </td>
                <td class="tabnum">{{ fmt(px.fires_7d || 0) }}</td>
                <td style="font-size:11px;color:#555">{{ px.last_fire ? relTime(px.last_fire) : '—' }}</td>
                <td style="font-size:11px;color:#666">{{ px.campaign_name || '—' }}</td>
                <td>
                  <span :class="px.active ? 'badge badge-active' : 'badge badge-paused'">{{ px.active ? 'active' : 'paused' }}</span>
                </td>
                <td>
                  <div style="display:flex;gap:4px">
                    <button @click="openPixelCode(px)" class="btn btn-ghost btn-xs">Code</button>
                    <button @click="togglePixel(px)" class="btn btn-ghost btn-xs">{{ px.active ? 'Pause' : 'Resume' }}</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ── TAB: POSTBACKS ── -->
    <div v-if="activeTab === 'postbacks'">
      <div class="card">
        <div class="card-header-row">
          <span class="card-label">S2S Postback URLs</span>
          <button @click="showAddPostback = true" class="btn btn-secondary btn-sm">Add Postback</button>
        </div>
        <div class="card-body-pad">
          <div class="info-box">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>
            </svg>
            <span>Postback fires on conversion. Macro <code class="inline-code">{click_id}</code> is required for attribution. Optionally pass <code class="inline-code">{revenue}</code> for ROI optimization.</span>
          </div>
        </div>
        <div v-if="!postbacks.length" class="empty-state">
          <div class="empty-icon">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
              <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
              <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
            </svg>
          </div>
          <div class="empty-title">No postbacks configured</div>
          <div class="empty-desc">Add a postback URL to receive conversion signals from your tracker</div>
        </div>
        <div v-else class="overflow-x-auto">
          <table class="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>URL</th>
                <th>Method</th>
                <th>Fires (7d)</th>
                <th>Last Fire</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="pb in postbacks" :key="pb.id">
                <td style="font-weight:500;color:#d0d0d0">{{ pb.name }}</td>
                <td style="max-width:280px">
                  <div class="url-cell">{{ pb.url }}</div>
                </td>
                <td><span class="badge badge-rtb">{{ pb.method }}</span></td>
                <td class="tabnum">{{ fmt(pb.fires_7d || 0) }}</td>
                <td style="font-size:11px;color:#555">{{ pb.last_fire ? relTime(pb.last_fire) : '—' }}</td>
                <td><span :class="pb.active ? 'badge badge-active' : 'badge badge-paused'">{{ pb.active ? 'active' : 'paused' }}</span></td>
                <td>
                  <button @click="confirmDeletePb(pb)" class="btn btn-ghost btn-xs" style="color:#f87171">Delete</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ── TAB: EVENT LOG ── -->
    <div v-if="activeTab === 'events'">
      <div class="card">
        <div class="card-header-row">
          <span class="card-label">Live Event Log</span>
          <div style="display:flex;gap:8px;align-items:center">
            <div class="live-dot" />
            <span style="font-size:11px;color:#444">Updates every 10s</span>
            <select v-model="eventType" class="select select-xs">
              <option value="">All events</option>
              <option value="impression">Impression</option>
              <option value="click">Click</option>
              <option value="conversion">Conversion</option>
              <option value="postback">Postback</option>
            </select>
          </div>
        </div>
        <div v-if="loadingEvents" style="padding:20px;display:flex;flex-direction:column;gap:6px">
          <div v-for="i in 6" :key="i" class="skeleton" style="height:36px" />
        </div>
        <div v-else-if="!events.length" class="empty-state">
          <div class="empty-title">No events yet</div>
          <div class="empty-desc">Events will stream here once pixels start firing</div>
        </div>
        <div v-else class="overflow-x-auto">
          <table class="data-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Type</th>
                <th>Click ID</th>
                <th>Campaign</th>
                <th>Country</th>
                <th>Device</th>
                <th>Revenue</th>
                <th>Attribution</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="ev in events" :key="ev.id"
                :class="ev.type === 'conversion' ? 'row-highlight' : ''">
                <td style="font-size:11px;color:#555;white-space:nowrap">{{ relTime(ev.created_at) }}</td>
                <td>
                  <span :class="`event-badge event-${ev.type}`">{{ ev.type }}</span>
                </td>
                <td><code class="code-tag">{{ ev.click_id?.slice(0,12) }}...</code></td>
                <td style="font-size:11px;color:#666">{{ ev.campaign_name || '—' }}</td>
                <td style="font-size:11px;color:#555">{{ ev.country || '—' }}</td>
                <td style="font-size:11px;color:#555">{{ ev.device || '—' }}</td>
                <td class="tabnum" :class="ev.revenue ? 'val-positive' : ''">
                  {{ ev.revenue ? '$' + Number(ev.revenue).toFixed(2) : '—' }}
                </td>
                <td>
                  <span v-if="ev.attributed" class="badge badge-active" style="font-size:10px">attributed</span>
                  <span v-else style="font-size:11px;color:#333">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ── TAB: DSP FEED ── -->
    <div v-if="activeTab === 'dsp_feed'">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">

        <!-- Attribution stats -->
        <div class="card">
          <div class="card-header-row"><span class="card-label">Attribution Window</span></div>
          <div class="card-body-pad">
            <div style="display:flex;flex-direction:column;gap:12px">
              <div v-for="w in attrWindows" :key="w.label" style="display:flex;align-items:center;justify-content:space-between">
                <span style="font-size:12px;color:#666">{{ w.label }}</span>
                <div style="display:flex;align-items:center;gap:8px">
                  <div style="width:80px;background:rgba(255,255,255,0.04);border-radius:3px;height:4px">
                    <div :style="{width: w.pct+'%',background:'#34d399',borderRadius:'3px',height:'4px'}" />
                  </div>
                  <span class="tabnum" style="font-size:12px;width:32px;text-align:right">{{ w.pct }}%</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- DSP optimization signals -->
        <div class="card">
          <div class="card-header-row"><span class="card-label">DSP Optimization Signals</span></div>
          <div class="card-body-pad">
            <div style="display:flex;flex-direction:column;gap:10px">
              <div v-for="sig in dspSignals" :key="sig.label" style="display:flex;align-items:flex-start;gap:10px">
                <div :class="`signal-dot ${sig.ok ? 'ok' : 'warn'}`" />
                <div>
                  <div style="font-size:12px;font-weight:500;color:#c0c0c0">{{ sig.label }}</div>
                  <div style="font-size:11px;color:#444;margin-top:2px">{{ sig.desc }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- CVR by context table -->
        <div class="card" style="grid-column:1/-1">
          <div class="card-header-row">
            <span class="card-label">CVR by Context — fed into Bidding Engine</span>
            <span style="font-size:11px;color:#444">Updates every 15 min · used for bid price calculation</span>
          </div>
          <div class="overflow-x-auto">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Dimension</th>
                  <th>Value</th>
                  <th>Impressions</th>
                  <th>Conversions</th>
                  <th>CVR</th>
                  <th>Avg Revenue</th>
                  <th>Bid Multiplier</th>
                  <th>Last Updated</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in cvrContext" :key="row.dim+row.val">
                  <td style="font-size:11px;color:#555">{{ row.dim }}</td>
                  <td style="font-weight:500;color:#c0c0c0">{{ row.val }}</td>
                  <td class="tabnum">{{ fmt(row.imp) }}</td>
                  <td class="tabnum">{{ fmt(row.conv) }}</td>
                  <td class="tabnum" :class="row.cvr > 2 ? 'val-positive' : ''">
                    {{ row.cvr.toFixed(2) }}%
                  </td>
                  <td class="tabnum val-positive">${{ row.avg_rev.toFixed(2) }}</td>
                  <td class="tabnum">
                    <span :class="row.mult > 1 ? 'val-positive' : row.mult < 1 ? 'val-negative' : ''">
                      x{{ row.mult.toFixed(2) }}
                    </span>
                  </td>
                  <td style="font-size:11px;color:#555">{{ row.updated }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

      </div>
    </div>

    <!-- Create Pixel Modal -->
    <Transition name="modal">
      <div v-if="showCreatePixel" class="modal-backdrop" @click.self="showCreatePixel = false">
        <div class="modal-panel">
          <div class="modal-header">
            <div class="modal-title">New Pixel</div>
            <button @click="showCreatePixel = false" class="btn btn-ghost btn-sm">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>
            </button>
          </div>
          <div class="modal-body">
            <div style="display:flex;flex-direction:column;gap:14px">
              <div class="form-row">
                <label class="form-label">Pixel name</label>
                <input v-model="newPixel.name" class="input" placeholder="e.g. Purchase pixel" />
              </div>
              <div class="form-row">
                <label class="form-label">Type</label>
                <div class="chip-group">
                  <button v-for="t in pixelTypes" :key="t"
                    @click="newPixel.type = t"
                    :class="['chip', newPixel.type === t ? 'active' : '']">
                    {{ t }}
                  </button>
                </div>
              </div>
              <div class="form-row">
                <label class="form-label">Bind to campaign (optional)</label>
                <input v-model="newPixel.campaign_id" class="input" placeholder="Campaign ID" />
              </div>
              <div v-if="newPixel.name" class="code-preview">
                <div style="font-size:11px;color:#444;margin-bottom:6px">Preview snippet</div>
                <pre class="code-block">&lt;img src="https://t.yourdsp.com/px?pid=NEW_ID&type={{ newPixel.type }}" width="1" height="1" /&gt;</pre>
              </div>
            </div>
          </div>
          <div class="modal-footer">
            <button @click="showCreatePixel = false" class="btn btn-secondary">Cancel</button>
            <button @click="createPixel" :disabled="creatingPixel || !newPixel.name" class="btn btn-primary">
              {{ creatingPixel ? 'Creating...' : 'Create Pixel' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>

    <!-- Pixel Code Modal -->
    <Transition name="modal">
      <div v-if="pixelCodeTarget" class="modal-backdrop" @click.self="pixelCodeTarget = null">
        <div class="modal-panel" style="max-width:560px">
          <div class="modal-header">
            <div class="modal-title">Pixel Code — {{ pixelCodeTarget?.name }}</div>
            <button @click="pixelCodeTarget = null" class="btn btn-ghost btn-sm">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>
            </button>
          </div>
          <div class="modal-body">
            <div style="display:flex;flex-direction:column;gap:14px">
              <div>
                <div class="code-label">Image pixel (1×1)</div>
                <div class="code-block">&lt;img src="https://t.yourdsp.com/px?pid={{ pixelCodeTarget?.pixel_id }}&click_id={CLICK_ID}" width="1" height="1" /&gt;</div>
                <button @click="copy(`<img src='https://t.yourdsp.com/px?pid=${pixelCodeTarget?.pixel_id}&click_id={CLICK_ID}' width='1' height='1' />`)" class="btn btn-ghost btn-xs" style="margin-top:6px">Copy</button>
              </div>
              <div>
                <div class="code-label">JS pixel</div>
                <div class="code-block">(function(){var i=new Image();i.src='https://t.yourdsp.com/px?pid={{ pixelCodeTarget?.pixel_id }}&click_id={CLICK_ID}&rev={REVENUE}'})();</div>
                <button @click="copy(`(function(){var i=new Image();i.src='https://t.yourdsp.com/px?pid=${pixelCodeTarget?.pixel_id}&click_id={CLICK_ID}&rev={REVENUE}'})();`)" class="btn btn-ghost btn-xs" style="margin-top:6px">Copy</button>
              </div>
              <div>
                <div class="code-label">S2S Postback URL</div>
                <div class="code-block">https://t.yourdsp.com/s2s?pid={{ pixelCodeTarget?.pixel_id }}&click_id={CLICK_ID}&status=1&rev={REVENUE}</div>
                <button @click="copy(`https://t.yourdsp.com/s2s?pid=${pixelCodeTarget?.pixel_id}&click_id={CLICK_ID}&status=1&rev={REVENUE}`)" class="btn btn-ghost btn-xs" style="margin-top:6px">Copy</button>
              </div>
              <div class="info-box">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
                <span>Replace <code class="inline-code">{CLICK_ID}</code> with your tracker's click_id macro. Revenue value is used for ROI calculation in the DSP.</span>
              </div>
            </div>
          </div>
          <div class="modal-footer">
            <button @click="pixelCodeTarget = null" class="btn btn-secondary">Close</button>
          </div>
        </div>
      </div>
    </Transition>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'

const props = defineProps({ period: String })

const activeTab       = ref('pixels')
const pixels          = ref([])
const postbacks       = ref([])
const events          = ref([])
const loadingPixels   = ref(true)
const loadingEvents   = ref(true)
const showCreatePixel = ref(false)
const showAddPostback = ref(false)
const creatingPixel   = ref(false)
const pixelCodeTarget = ref(null)
const eventType       = ref('')
let   eventTimer      = null

const newPixel = ref({ name: '', type: 'conversion', campaign_id: '' })
const pixelTypes = ['impression', 'click', 'conversion', 'custom']

const fmt = n => Number(n || 0).toLocaleString('en-US')

function relTime(ts) {
  const d = Date.now() - new Date(ts).getTime()
  if (d < 60000)  return Math.floor(d/1000) + 's ago'
  if (d < 3600000) return Math.floor(d/60000) + 'm ago'
  if (d < 86400000) return Math.floor(d/3600000) + 'h ago'
  return Math.floor(d/86400000) + 'd ago'
}

function copy(text) {
  navigator.clipboard?.writeText(text)
}

const tabs = computed(() => [
  { v: 'pixels',    l: 'Pixels',    count: pixels.value.length },
  { v: 'postbacks', l: 'Postbacks', count: postbacks.value.length },
  { v: 'events',    l: 'Event Log' },
  { v: 'dsp_feed',  l: 'DSP Feed' },
])

// Static demo data for DSP feed tab
const attrWindows = [
  { label: 'Click — 1 day',   pct: 54 },
  { label: 'Click — 7 days',  pct: 28 },
  { label: 'View — 1 day',    pct: 12 },
  { label: 'View — 7 days',   pct: 6  },
]
const dspSignals = [
  { ok: true,  label: 'Conversion feed active',   desc: 'Postback signals flowing into bidding engine' },
  { ok: true,  label: 'CVR model updated',         desc: 'Last update 14 min ago · 3,241 samples' },
  { ok: true,  label: 'Bid multipliers applied',   desc: 'x1.4 on US/iOS · x0.7 on Android/Tier3' },
  { ok: false, label: 'Revenue data incomplete',   desc: 'rev macro missing in 34% of postbacks' },
  { ok: true,  label: 'Attribution window set',    desc: 'Click 7d / View 1d' },
]
const cvrContext = ref([
  { dim:'country', val:'US',      imp:48210, conv:312, cvr:0.65, avg_rev:4.20, mult:1.40, updated:'2m ago' },
  { dim:'country', val:'GB',      imp:21300, conv:118, cvr:0.55, avg_rev:3.80, mult:1.20, updated:'2m ago' },
  { dim:'country', val:'DE',      imp:19800, conv:97,  cvr:0.49, avg_rev:3.60, mult:1.10, updated:'2m ago' },
  { dim:'country', val:'IN',      imp:34500, conv:62,  cvr:0.18, avg_rev:0.80, mult:0.60, updated:'2m ago' },
  { dim:'device',  val:'Desktop', imp:52100, conv:298, cvr:0.57, avg_rev:3.90, mult:1.30, updated:'2m ago' },
  { dim:'device',  val:'Mobile',  imp:61200, conv:291, cvr:0.48, avg_rev:2.40, mult:1.00, updated:'2m ago' },
  { dim:'os',      val:'iOS',     imp:29800, conv:198, cvr:0.66, avg_rev:4.10, mult:1.40, updated:'2m ago' },
  { dim:'os',      val:'Android', imp:31400, conv:93,  cvr:0.30, avg_rev:1.80, mult:0.70, updated:'2m ago' },
])

function openPixelCode(px) { pixelCodeTarget.value = px }

async function loadPixels() {
  loadingPixels.value = true
  try {
    const r = await fetch('/api/pixels')
    if (r.ok) pixels.value = await r.json()
  } catch {}
  finally { loadingPixels.value = false }
}

async function loadPostbacks() {
  try {
    const r = await fetch('/api/postbacks')
    if (r.ok) postbacks.value = await r.json()
  } catch {}
}

async function loadEvents() {
  loadingEvents.value = events.value.length === 0
  try {
    const q = eventType.value ? `?type=${eventType.value}` : ''
    const r = await fetch(`/api/events${q}`)
    if (r.ok) events.value = await r.json()
  } catch {}
  finally { loadingEvents.value = false }
}

async function createPixel() {
  creatingPixel.value = true
  try {
    const r = await fetch('/api/pixels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newPixel.value),
    })
    if (r.ok) {
      showCreatePixel.value = false
      newPixel.value = { name: '', type: 'conversion', campaign_id: '' }
      await loadPixels()
    }
  } catch {}
  finally { creatingPixel.value = false }
}

async function togglePixel(px) {
  await fetch(`/api/pixels/${px.id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ active: !px.active }),
  })
  await loadPixels()
}

function confirmDeletePb(pb) {
  if (confirm(`Delete postback "${pb.name}"?`)) {
    fetch(`/api/postbacks/${pb.id}`, { method: 'DELETE' }).then(loadPostbacks)
  }
}

onMounted(() => {
  loadPixels()
  loadPostbacks()
  loadEvents()
  eventTimer = setInterval(loadEvents, 10000)
})
onUnmounted(() => clearInterval(eventTimer))
watch(eventType, loadEvents)
</script>

<style scoped>
.flow-banner {
  display:flex;align-items:center;gap:0;
  background:#0e0e0e;border:1px solid rgba(255,255,255,0.06);
  border-radius:10px;padding:14px 20px;
}
.flow-step {
  display:flex;flex-direction:column;align-items:center;
  gap:4px;padding:0 16px;
  opacity:0.5;transition:opacity 180ms;
}
.flow-step.active { opacity:1; }
.flow-icon {
  width:28px;height:28px;border-radius:7px;
  background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);
  display:flex;align-items:center;justify-content:center;color:#888;
}
.flow-step.active .flow-icon { background:rgba(52,211,153,0.1);border-color:rgba(52,211,153,0.3);color:#34d399; }
.flow-label { font-size:11px;font-weight:600;color:#888; }
.flow-step.active .flow-label { color:#e0e0e0; }
.flow-sub   { font-size:10px;color:#3d3d3d; }
.flow-arrow { color:#2a2a2a;font-size:16px;padding:0 4px; }

.tab-bar { display:flex;gap:2px;border-bottom:1px solid rgba(255,255,255,0.06);margin-bottom:0; }
.tab-btn {
  padding:8px 14px;font-size:12px;color:#555;border:none;
  background:none;cursor:pointer;border-bottom:2px solid transparent;
  margin-bottom:-1px;transition:all 150ms;display:flex;align-items:center;gap:5px;
}
.tab-btn:hover { color:#999; }
.tab-btn.active { color:#e0e0e0;border-bottom-color:#e0e0e0; }
.tab-count {
  background:rgba(255,255,255,0.06);border-radius:10px;
  padding:0 6px;font-size:10px;color:#555;
}

.card-header-row {
  display:flex;align-items:center;justify-content:space-between;
  padding:14px 16px;border-bottom:1px solid rgba(255,255,255,0.05);
}
.card-label { font-size:12px;font-weight:600;color:#888; }
.card-body-pad { padding:14px 16px; }

.info-box {
  display:flex;align-items:flex-start;gap:8px;
  background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);
  border-radius:7px;padding:10px 12px;
  font-size:11px;color:#555;line-height:1.5;
}
.inline-code {
  background:rgba(255,255,255,0.06);border-radius:3px;
  padding:1px 4px;font-size:10px;font-family:monospace;color:#a0a0a0;
}

.code-tag {
  background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.07);
  border-radius:4px;padding:2px 6px;font-size:10px;font-family:monospace;color:#888;
}
.url-cell {
  font-size:10px;font-family:monospace;color:#555;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:260px;
}
.icon-btn {
  padding:3px;border-radius:4px;color:#444;
  transition:color 150ms,background 150ms;
}
.icon-btn:hover { color:#888;background:rgba(255,255,255,0.06); }

.live-dot {
  width:6px;height:6px;border-radius:50%;
  background:#34d399;
  box-shadow:0 0 6px #34d399;
  animation:pulse 2s infinite;
}
@keyframes pulse {
  0%,100%{opacity:1}50%{opacity:0.4}
}

.event-badge {
  font-size:10px;padding:2px 6px;border-radius:4px;font-weight:600;
}
.event-impression { background:rgba(96,165,250,0.1);color:#60a5fa; }
.event-click      { background:rgba(251,191,36,0.1);color:#fbbf24; }
.event-conversion { background:rgba(52,211,153,0.1);color:#34d399; }
.event-postback   { background:rgba(167,139,250,0.1);color:#a78bfa; }
.row-highlight td { background:rgba(52,211,153,0.03); }

.signal-dot {
  width:7px;height:7px;border-radius:50%;margin-top:3px;flex-shrink:0;
}
.signal-dot.ok   { background:#34d399; }
.signal-dot.warn { background:#f87171; }

.code-label { font-size:10px;color:#444;margin-bottom:5px;font-weight:600;text-transform:uppercase;letter-spacing:0.04em; }
.code-block {
  background:#0a0a0a;border:1px solid rgba(255,255,255,0.07);
  border-radius:7px;padding:10px 12px;font-size:10px;
  font-family:monospace;color:#888;overflow-x:auto;
  white-space:pre;line-height:1.6;
}
.code-preview { background:rgba(255,255,255,0.02);border-radius:7px;padding:12px; }

.select-xs {
  background:#111;border:1px solid rgba(255,255,255,0.08);
  border-radius:6px;padding:4px 8px;font-size:11px;color:#888;
}

.modal-backdrop {
  position:fixed;inset:0;background:rgba(0,0,0,0.7);
  backdrop-filter:blur(4px);display:flex;
  align-items:center;justify-content:center;z-index:200;padding:24px;
}
.modal-panel {
  background:#111;border:1px solid rgba(255,255,255,0.09);
  border-radius:14px;width:100%;max-width:520px;max-height:90vh;
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
