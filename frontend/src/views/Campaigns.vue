<template>
  <div class="page-wrap">

    <!-- Header -->
    <div class="page-header">
      <div>
        <h1 class="page-title">Campaigns</h1>
        <p class="page-subtitle">Manage and monitor all advertising campaigns</p>
      </div>
      <button @click="showCreate = true" class="btn btn-primary">New Campaign</button>
    </div>

    <!-- Filters -->
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <div style="position:relative;flex:1;min-width:180px;max-width:280px">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
          style="position:absolute;left:10px;top:50%;transform:translateY(-50%);color:#444;pointer-events:none">
          <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
        </svg>
        <input v-model="search" class="input" style="padding-left:30px" placeholder="Search campaigns..." />
      </div>
      <div class="chip-group">
        <button v-for="f in statusFilters" :key="f.v"
          @click="activeFilter = f.v"
          :class="['chip', activeFilter === f.v ? 'active' : '']">
          {{ f.l }}
          <span v-if="f.v !== 'all'" style="margin-left:4px;opacity:0.5">{{ countByStatus(f.v) }}</span>
        </button>
      </div>
    </div>

    <!-- Table -->
    <div class="card">
      <div v-if="loading" class="card-body space-y-2">
        <div v-for="i in 5" :key="i" class="skeleton" style="height:44px" />
      </div>

      <div v-else-if="!filtered.length" class="empty-state">
        <div class="empty-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path d="M3 6h18M3 12h18M3 18h12"/>
          </svg>
        </div>
        <div class="empty-title">No campaigns found</div>
        <div class="empty-desc">Try changing the filter or create a new campaign</div>
        <button @click="showCreate = true" class="btn btn-secondary btn-sm" style="margin-top:16px">New Campaign</button>
      </div>

      <div v-else class="overflow-x-auto">
        <table class="data-table">
          <thead>
            <tr>
              <th>Campaign</th>
              <th>Type</th>
              <th>Status</th>
              <th>Budget</th>
              <th>Spent</th>
              <th>Impressions</th>
              <th>Clicks</th>
              <th>CTR</th>
              <th>Conv</th>
              <th>ROI</th>
              <th>Win Rate</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in filtered" :key="c.id">
              <td>
                <div style="font-weight:500;color:#d0d0d0;max-width:200px;overflow:hidden;text-overflow:ellipsis">
                  {{ c.name }}
                </div>
                <div style="font-size:11px;color:#3d3d3d;margin-top:1px">ID: {{ c.id }}</div>
              </td>
              <td>
                <span class="badge badge-rtb" v-if="c.type === 'rtb'">RTB</span>
                <span class="badge badge-draft" v-else>Direct</span>
              </td>
              <td><span :class="`badge badge-${c.status}`">{{ c.status }}</span></td>
              <td>
                <div class="tabnum" style="font-size:12px">${{ c.daily_spent || '0.00' }} / ${{ c.daily_budget }}</div>
                <div class="progress-track" style="margin-top:4px;width:72px">
                  <div class="progress-fill"
                    :class="budgetPct(c) > 90 ? 'red' : budgetPct(c) > 70 ? 'amber' : 'green'"
                    :style="{ width: Math.min(100, budgetPct(c)) + '%' }" />
                </div>
              </td>
              <td class="tabnum">${{ c.spend || '0.00' }}</td>
              <td class="tabnum">{{ fmt(c.impressions || 0) }}</td>
              <td class="tabnum">{{ fmt(c.clicks || 0) }}</td>
              <td class="tabnum" :class="(c.ctr||0) > 1 ? 'val-positive' : ''">
                {{ (c.ctr || 0).toFixed(2) }}%
              </td>
              <td class="tabnum">{{ fmt(c.conversions || 0) }}</td>
              <td class="tabnum" :class="(c.roi||0) >= 0 ? 'val-positive' : 'val-negative'">
                {{ c.roi || 0 }}%
              </td>
              <td class="tabnum val-accent">{{ c.win_rate || 0 }}%</td>
              <td>
                <div style="display:flex;gap:4px">
                  <button v-if="c.status === 'active'"
                    @click="setStatus(c.id, 'paused')" class="btn btn-ghost btn-xs">Pause</button>
                  <button v-if="c.status === 'paused'"
                    @click="setStatus(c.id, 'active')" class="btn btn-ghost btn-xs">Resume</button>
                  <button @click="confirmDelete(c)" class="btn btn-ghost btn-xs" style="color:#f87171">Delete</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Create campaign modal -->
    <Transition name="modal">
      <div v-if="showCreate" class="modal-backdrop" @click.self="showCreate = false">
        <div class="modal-panel">
          <div class="modal-header">
            <div class="modal-title">New Campaign</div>
            <button @click="showCreate = false" class="btn btn-ghost btn-sm">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M18 6 6 18M6 6l12 12"/>
              </svg>
            </button>
          </div>
          <div class="modal-body">
            <div style="display:flex;flex-direction:column;gap:14px">
              <div class="form-row">
                <label class="form-label">Campaign name</label>
                <input v-model="newCamp.name" class="input" placeholder="e.g. Brand Awareness Q3" />
              </div>
              <div class="form-row">
                <label class="form-label">Type</label>
                <div class="chip-group">
                  <button v-for="t in ['rtb', 'direct']" :key="t"
                    @click="newCamp.type = t"
                    :class="['chip', newCamp.type === t ? 'active' : '']">
                    {{ t.toUpperCase() }}
                  </button>
                </div>
              </div>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
                <div class="form-row">
                  <label class="form-label">Daily budget ($)</label>
                  <input v-model="newCamp.daily_budget" type="number" class="input" placeholder="100" />
                </div>
                <div class="form-row">
                  <label class="form-label">Total budget ($)</label>
                  <input v-model="newCamp.total_budget" type="number" class="input" placeholder="3000" />
                </div>
              </div>
              <div class="form-row">
                <label class="form-label">Start date</label>
                <input v-model="newCamp.start_date" type="date" class="input" />
              </div>
              <div v-if="createError" class="error-alert">{{ createError }}</div>
            </div>
          </div>
          <div class="modal-footer">
            <button @click="showCreate = false" class="btn btn-secondary">Cancel</button>
            <button @click="createCampaign" :disabled="creating || !newCamp.name || !newCamp.daily_budget"
              class="btn btn-primary">
              {{ creating ? 'Creating...' : 'Create Campaign' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>

    <!-- Delete confirm modal -->
    <Transition name="modal">
      <div v-if="deleteTarget" class="modal-backdrop" @click.self="deleteTarget = null">
        <div class="modal-panel" style="max-width:400px">
          <div class="modal-header">
            <div class="modal-title">Delete Campaign</div>
          </div>
          <div class="modal-body">
            <p style="font-size:13px;color:#888;line-height:1.6">
              Are you sure you want to delete
              <span style="color:#d0d0d0;font-weight:600">{{ deleteTarget?.name }}</span>?
              This action cannot be undone.
            </p>
          </div>
          <div class="modal-footer">
            <button @click="deleteTarget = null" class="btn btn-secondary">Cancel</button>
            <button @click="deleteCampaign" :disabled="deleting" class="btn btn-danger">
              {{ deleting ? 'Deleting...' : 'Delete' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'

const props = defineProps({ period: String })

const campaigns   = ref([])
const loading     = ref(true)
const search      = ref('')
const activeFilter = ref('all')
const showCreate  = ref(false)
const creating    = ref(false)
const createError = ref('')
const deleteTarget = ref(null)
const deleting    = ref(false)

const statusFilters = [
  { v: 'all',    l: 'All' },
  { v: 'active', l: 'Active' },
  { v: 'paused', l: 'Paused' },
  { v: 'draft',  l: 'Draft' },
]

const newCamp = ref({
  name: '', type: 'rtb',
  daily_budget: '', total_budget: '', start_date: '',
})

const fmt = n => Number(n).toLocaleString('en-US')
const budgetPct = c => c.daily_budget ? (c.daily_spent / c.daily_budget) * 100 : 0
const countByStatus = s => campaigns.value.filter(c => c.status === s).length

const filtered = computed(() => {
  let list = campaigns.value
  if (activeFilter.value !== 'all') list = list.filter(c => c.status === activeFilter.value)
  if (search.value) {
    const q = search.value.toLowerCase()
    list = list.filter(c => c.name.toLowerCase().includes(q))
  }
  return list
})

async function load() {
  loading.value = true
  try {
    const r = await fetch(`/api/campaigns?period=${props.period || '7d'}`)
    if (r.ok) campaigns.value = await r.json()
  } catch (e) { console.warn(e) }
  finally { loading.value = false }
}

async function createCampaign() {
  creating.value = true
  createError.value = ''
  try {
    const r = await fetch('/api/campaigns', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name:         newCamp.value.name,
        type:         newCamp.value.type,
        daily_budget: parseFloat(newCamp.value.daily_budget),
        total_budget: parseFloat(newCamp.value.total_budget) || null,
        start_date:   newCamp.value.start_date || null,
      }),
    })
    if (!r.ok) throw new Error((await r.json()).detail || 'Create failed')
    showCreate.value = false
    Object.assign(newCamp.value, { name:'', daily_budget:'', total_budget:'', start_date:'' })
    await load()
  } catch (e) {
    createError.value = e.message
  } finally {
    creating.value = false
  }
}

async function setStatus(id, status) {
  await fetch(`/api/campaigns/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  })
  await load()
}

const confirmDelete = c => { deleteTarget.value = c }
async function deleteCampaign() {
  deleting.value = true
  try {
    await fetch(`/api/campaigns/${deleteTarget.value.id}`, { method: 'DELETE' })
    deleteTarget.value = null
    await load()
  } catch (e) { console.warn(e) }
  finally { deleting.value = false }
}

onMounted(load)
watch(() => props.period, load)
</script>

<style scoped>
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
.error-alert {
  background:rgba(248,113,113,0.08);border:1px solid rgba(248,113,113,0.2);
  border-radius:7px;padding:10px 14px;font-size:12px;color:#f87171;
}
.modal-enter-active { transition:all 180ms cubic-bezier(0.16,1,0.3,1); }
.modal-leave-active { transition:all 120ms ease-in; }
.modal-enter-from { opacity:0;transform:scale(0.97) translateY(8px); }
.modal-leave-to   { opacity:0;transform:scale(0.98) translateY(4px); }
</style>
