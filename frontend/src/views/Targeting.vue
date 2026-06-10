<template>
  <div class="page-wrap">

    <!-- Header -->
    <div class="page-header">
      <div>
        <h1 class="page-title">Targeting</h1>
        <p class="page-subtitle">Per-campaign bid floors, geo, device, OS and hour schedule</p>
      </div>
    </div>

    <!-- Campaign selector -->
    <div class="card" style="padding:14px 16px">
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <span class="form-label" style="white-space:nowrap">Campaign</span>
        <select v-model="selectedCampaignId" class="select" style="max-width:320px" @change="loadTargeting">
          <option value="">— select campaign —</option>
          <option v-for="c in campaigns" :key="c.id" :value="c.id">{{ c.name }}</option>
        </select>
        <span v-if="loadingCampaigns" style="font-size:11px;color:var(--text-muted)">Loading…</span>
      </div>
    </div>

    <!-- Empty: no campaign selected -->
    <div v-if="!selectedCampaignId" class="card">
      <div class="empty-state">
        <div class="empty-icon">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>
          </svg>
        </div>
        <div class="empty-title">Select a campaign</div>
        <div class="empty-desc">Targeting rules are attached per-campaign. Choose one above to view or edit.</div>
      </div>
    </div>

    <!-- Editor -->
    <template v-else>

      <!-- Bid -->
      <div class="t-section">
        <div class="t-section-title">Bid Limits</div>
        <div class="t-grid-2">
          <div class="form-row">
            <label class="form-label">Floor CPM ($)</label>
            <input v-model.number="form.bid_floor" type="number" step="0.01" min="0" class="input" placeholder="0.00" />
          </div>
          <div class="form-row">
            <label class="form-label">Ceiling CPM ($)</label>
            <input v-model.number="form.bid_ceiling" type="number" step="0.01" min="0" class="input" placeholder="10.00" />
          </div>
        </div>
      </div>

      <!-- Geo -->
      <div class="t-section">
        <div class="t-section-title">Geo Countries (ISO-2)</div>
        <div class="tag-input-wrap">
          <div class="tag-list">
            <span v-for="(c,i) in form.geo_countries" :key="c" class="tag">
              {{ c }}
              <button @click="form.geo_countries.splice(i,1)" class="tag-remove">&times;</button>
            </span>
            <input
              v-model="geoInput"
              @keydown.enter.prevent="addGeo"
              @keydown=",.prevent="addGeo""
              class="tag-text-input"
              placeholder="US, DE, RU…  Enter to add"
              maxlength="2"
            />
          </div>
        </div>
        <div v-if="!form.geo_countries.length" style="font-size:11px;color:var(--text-faint);margin-top:4px">Empty = all countries allowed</div>
      </div>

      <!-- Devices -->
      <div class="t-section">
        <div class="t-section-title">Device Types</div>
        <div class="chip-group">
          <button
            v-for="d in DEVICES" :key="d"
            @click="toggleList(form.device_types, d)"
            :class="['chip', form.device_types.includes(d) ? 'active' : '']">
            {{ d }}
          </button>
        </div>
        <div v-if="!form.device_types.length" style="font-size:11px;color:var(--text-faint);margin-top:6px">Empty = all devices</div>
      </div>

      <!-- OS -->
      <div class="t-section">
        <div class="t-section-title">Operating Systems</div>
        <div class="chip-group">
          <button
            v-for="os in OS_LIST" :key="os"
            @click="toggleList(form.os_list, os)"
            :class="['chip', form.os_list.includes(os) ? 'active' : '']">
            {{ os }}
          </button>
        </div>
      </div>

      <!-- Browsers -->
      <div class="t-section">
        <div class="t-section-title">Browsers</div>
        <div class="chip-group">
          <button
            v-for="b in BROWSERS" :key="b"
            @click="toggleList(form.browser_list, b)"
            :class="['chip', form.browser_list.includes(b) ? 'active' : '']">
            {{ b }}
          </button>
        </div>
      </div>

      <!-- Site lists -->
      <div class="t-section">
        <div class="t-section-title">Site Lists</div>
        <div class="t-grid-2">
          <div class="form-row">
            <label class="form-label">Whitelist (one domain per line)</label>
            <textarea
              v-model="whitelistText"
              class="textarea"
              rows="5"
              placeholder="example.com
news.site.org"
            />
          </div>
          <div class="form-row">
            <label class="form-label">Blacklist (one domain per line)</label>
            <textarea
              v-model="blacklistText"
              class="textarea"
              rows="5"
              placeholder="bad-traffic.biz"
            />
          </div>
        </div>
      </div>

      <!-- Hour schedule -->
      <div class="t-section">
        <div class="t-section-title" style="display:flex;align-items:center;justify-content:space-between">
          <span>Hour Schedule (UTC)</span>
          <div style="display:flex;gap:8px">
            <button class="btn btn-ghost btn-xs" @click="fillAllHours">All on</button>
            <button class="btn btn-ghost btn-xs" @click="clearAllHours">All off</button>
          </div>
        </div>
        <div class="hour-grid">
          <div v-for="day in 7" :key="day" class="hour-row">
            <div class="hour-day-label">{{ DAY_NAMES[day-1] }}</div>
            <div class="hour-cells">
              <button
                v-for="h in 24" :key="h-1"
                @click="toggleHour(day-1, h-1)"
                :class="['hour-cell', isHourOn(day-1, h-1) ? 'on' : '']">
                {{ String(h-1).padStart(2,'0') }}
              </button>
            </div>
          </div>
        </div>
        <div style="font-size:11px;color:var(--text-faint);margin-top:6px">Empty schedule = serve 24/7</div>
      </div>

      <!-- Save / Reset -->
      <div style="display:flex;gap:10px;justify-content:flex-end">
        <button @click="resetForm" class="btn btn-secondary">Reset</button>
        <button @click="saveTargeting" :disabled="saving" class="btn btn-primary">
          {{ saving ? 'Saving…' : 'Save Targeting' }}
        </button>
      </div>

      <!-- Toast -->
      <Transition name="toast">
        <div v-if="toast" :class="['toast', toast.type]">{{ toast.msg }}</div>
      </Transition>

    </template>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, watch, computed } from 'vue'

const props = defineProps({ period: String })

const DEVICES  = ['mobile', 'desktop', 'tablet', 'ctv', 'other']
const OS_LIST  = ['android', 'ios', 'windows', 'macos', 'linux', 'other']
const BROWSERS = ['chrome', 'safari', 'firefox', 'edge', 'samsung', 'other']
const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

const campaigns          = ref([])
const loadingCampaigns   = ref(true)
const selectedCampaignId = ref('')
const saving             = ref(false)
const toast              = ref(null)

const form = reactive({
  bid_floor:     0,
  bid_ceiling:   10,
  geo_countries: [],
  device_types:  [],
  os_list:       [],
  browser_list:  [],
  site_whitelist: [],
  site_blacklist: [],
  hour_schedule:  {}, // { "0": [8,9,10], "1": [...] }  key=day 0=Mon
})

const geoInput       = ref('')
const whitelistText  = ref('')
const blacklistText  = ref('')

// sync textarea ↔ form arrays
watch(whitelistText, v => { form.site_whitelist = v.split('\n').map(s=>s.trim()).filter(Boolean) })
watch(blacklistText, v => { form.site_blacklist = v.split('\n').map(s=>s.trim()).filter(Boolean) })

// ── helpers ──
function toggleList(arr, val) {
  const i = arr.indexOf(val)
  if (i === -1) arr.push(val); else arr.splice(i, 1)
}

function addGeo() {
  const v = geoInput.value.trim().toUpperCase().replace(/[^A-Z]/g,'')
  if (v.length === 2 && !form.geo_countries.includes(v)) form.geo_countries.push(v)
  geoInput.value = ''
}

function isHourOn(day, hour) {
  return (form.hour_schedule[String(day)] || []).includes(hour)
}
function toggleHour(day, hour) {
  const key = String(day)
  if (!form.hour_schedule[key]) form.hour_schedule[key] = []
  const arr = form.hour_schedule[key]
  const i = arr.indexOf(hour)
  if (i === -1) arr.push(hour); else arr.splice(i, 1)
  if (!arr.length) delete form.hour_schedule[key]
}
function fillAllHours() {
  const hours = Array.from({length:24},(_,i)=>i)
  for (let d=0;d<7;d++) form.hour_schedule[String(d)] = [...hours]
}
function clearAllHours() {
  Object.keys(form.hour_schedule).forEach(k => delete form.hour_schedule[k])
}

// ── API ──
async function loadCampaigns() {
  loadingCampaigns.value = true
  try {
    const r = await fetch('/api/campaigns')
    if (r.ok) campaigns.value = await r.json()
  } catch {}
  finally { loadingCampaigns.value = false }
}

async function loadTargeting() {
  if (!selectedCampaignId.value) return
  try {
    const r = await fetch(`/api/targeting/${selectedCampaignId.value}`)
    if (r.ok) {
      const d = await r.json()
      applyData(d)
    } else if (r.status === 404) {
      // no rules yet — reset to defaults
      resetForm()
    }
  } catch {}
}

function applyData(d) {
  form.bid_floor     = d.bid_floor     ?? 0
  form.bid_ceiling   = d.bid_ceiling   ?? 10
  form.geo_countries = d.geo_countries ?? []
  form.device_types  = d.device_types  ?? []
  form.os_list       = d.os_list       ?? []
  form.browser_list  = d.browser_list  ?? []
  form.site_whitelist = d.site_whitelist ?? []
  form.site_blacklist = d.site_blacklist ?? []
  // clear and re-assign hour_schedule keys
  Object.keys(form.hour_schedule).forEach(k => delete form.hour_schedule[k])
  Object.assign(form.hour_schedule, d.hour_schedule ?? {})
  whitelistText.value = (d.site_whitelist || []).join('\n')
  blacklistText.value = (d.site_blacklist || []).join('\n')
}

function resetForm() {
  form.bid_floor = 0; form.bid_ceiling = 10
  form.geo_countries = []; form.device_types = []
  form.os_list = []; form.browser_list = []
  form.site_whitelist = []; form.site_blacklist = []
  Object.keys(form.hour_schedule).forEach(k => delete form.hour_schedule[k])
  whitelistText.value = ''; blacklistText.value = ''
}

async function saveTargeting() {
  if (!selectedCampaignId.value) return
  saving.value = true
  try {
    const body = {
      bid_floor:      form.bid_floor,
      bid_ceiling:    form.bid_ceiling,
      geo_countries:  form.geo_countries,
      device_types:   form.device_types,
      os_list:        form.os_list,
      browser_list:   form.browser_list,
      site_whitelist: form.site_whitelist,
      site_blacklist: form.site_blacklist,
      hour_schedule:  { ...form.hour_schedule },
    }
    const r = await fetch(`/api/targeting/${selectedCampaignId.value}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (r.ok) {
      showToast('Targeting saved', 'success')
    } else {
      const e = await r.json().catch(() => ({}))
      showToast(e.detail || 'Save failed', 'error')
    }
  } catch {
    showToast('Network error', 'error')
  } finally {
    saving.value = false
  }
}

function showToast(msg, type = 'success') {
  toast.value = { msg, type }
  setTimeout(() => { toast.value = null }, 3000)
}

onMounted(loadCampaigns)
</script>

<style scoped>
.t-section {
  background: var(--sidebar-bg, #111);
  border: 1px solid var(--sidebar-border, rgba(255,255,255,0.07));
  border-radius: 10px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.t-section-title {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-muted, #666);
}
.t-grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width:640px) { .t-grid-2 { grid-template-columns: 1fr; } }

/* Tag input */
.tag-input-wrap {
  border: 1px solid var(--sidebar-border, rgba(255,255,255,0.1));
  border-radius: 7px;
  padding: 6px 8px;
  background: var(--bg, #0a0a0a);
  cursor: text;
}
.tag-list { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
.tag {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 8px;
  border-radius: 5px;
  background: var(--active-bg, rgba(255,255,255,0.1));
  color: var(--text, #d0d0d0);
  font-size: 11px; font-weight: 600;
}
.tag-remove {
  background: none; border: none; cursor: pointer;
  color: var(--text-muted, #666); font-size: 13px; line-height: 1;
  padding: 0; display: flex; align-items: center;
}
.tag-remove:hover { color: var(--text, #d0d0d0); }
.tag-text-input {
  background: none; border: none; outline: none;
  font-size: 12px; color: var(--text, #d0d0d0);
  min-width: 140px; padding: 2px 4px;
}
.tag-text-input::placeholder { color: var(--text-faint, #333); }

/* Hour grid */
.hour-grid { display: flex; flex-direction: column; gap: 4px; }
.hour-row  { display: flex; align-items: center; gap: 6px; }
.hour-day-label {
  width: 28px; font-size: 10px; font-weight: 700;
  color: var(--text-muted, #555); text-transform: uppercase;
  flex-shrink: 0;
}
.hour-cells { display: flex; flex-wrap: wrap; gap: 3px; }
.hour-cell {
  width: 26px; height: 22px;
  border-radius: 4px;
  font-size: 9px; font-variant-numeric: tabular-nums;
  background: var(--hover-bg, rgba(255,255,255,0.04));
  border: 1px solid var(--sidebar-border, rgba(255,255,255,0.07));
  color: var(--text-faint, #444);
  cursor: pointer;
  transition: background 100ms, color 100ms;
  display: flex; align-items: center; justify-content: center;
}
.hour-cell.on {
  background: rgba(52,211,153,0.15);
  border-color: rgba(52,211,153,0.3);
  color: #34d399;
}
.hour-cell:hover { background: var(--active-bg, rgba(255,255,255,0.08)); }

/* Toast */
.toast {
  position: fixed; bottom: 24px; right: 24px;
  padding: 10px 16px;
  border-radius: 8px;
  font-size: 13px; font-weight: 500;
  z-index: 1000;
  box-shadow: 0 4px 16px rgba(0,0,0,0.3);
}
.toast.success { background: rgba(52,211,153,0.15); border:1px solid rgba(52,211,153,0.3); color: #34d399; }
.toast.error   { background: rgba(248,113,113,0.15); border:1px solid rgba(248,113,113,0.3); color: #f87171; }
.toast-enter-active,.toast-leave-active { transition: all 250ms ease; }
.toast-enter-from,.toast-leave-to { opacity:0; transform:translateY(8px); }
</style>
