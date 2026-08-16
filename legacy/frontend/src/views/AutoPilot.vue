<template>
  <div class="page-wrap">

    <!-- Header -->
    <div class="page-header">
      <div>
        <h1 class="page-title">AutoPilot</h1>
        <p class="page-subtitle">Automated rules that react to performance signals and adjust campaigns in real time</p>
      </div>
      <button @click="showCreate = true" class="btn btn-primary">New Rule</button>
    </div>

    <!-- Stats strip -->
    <div class="stats-strip">
      <div class="stat-item">
        <span class="stat-val">{{ rules.filter(r=>r.active).length }}</span>
        <span class="stat-label">Active rules</span>
      </div>
      <div class="stat-divider"/>
      <div class="stat-item">
        <span class="stat-val">{{ todayFires }}</span>
        <span class="stat-label">Fires today</span>
      </div>
      <div class="stat-divider"/>
      <div class="stat-item">
        <span class="stat-val tabnum" style="color:#34d399">+{{ savedBudget }}</span>
        <span class="stat-label">Estimated budget saved</span>
      </div>
      <div class="stat-divider"/>
      <div class="stat-item">
        <span class="stat-val">{{ lastFire || '—' }}</span>
        <span class="stat-label">Last execution</span>
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

    <!-- ── TAB: RULES ── -->
    <div v-if="activeTab === 'rules'">
      <div v-if="!rules.length" class="empty-state">
        <div class="empty-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path d="M12 2a10 10 0 1 0 10 10"/>
            <path d="M12 6v6l4 2"/>
            <path d="M18 2v4h4"/>
          </svg>
        </div>
        <div class="empty-title">No rules yet</div>
        <div class="empty-desc">Create a rule to automate budget control, bidding and pausing</div>
        <button @click="showCreate = true" class="btn btn-secondary btn-sm" style="margin-top:16px">New Rule</button>
      </div>

      <div v-else style="display:flex;flex-direction:column;gap:8px">
        <div v-for="rule in rules" :key="rule.id" class="rule-card">
          <div class="rule-left">
            <div class="rule-toggle">
              <button @click="toggleRule(rule)"
                :class="['toggle-btn', rule.active ? 'on' : 'off']">
                <span class="toggle-knob"/>
              </button>
            </div>
            <div class="rule-info">
              <div class="rule-name">{{ rule.name }}</div>
              <div class="rule-logic">
                <span class="rule-tag trigger">IF</span>
                <span class="rule-condition">{{ rule.trigger_label }}</span>
                <span class="rule-tag action">THEN</span>
                <span class="rule-condition">{{ rule.action_label }}</span>
              </div>
              <div class="rule-meta">
                <span>Scope: {{ rule.scope }}</span>
                <span class="meta-dot">·</span>
                <span>Check every {{ rule.check_interval }}</span>
                <span class="meta-dot">·</span>
                <span>Fires: <b>{{ rule.fire_count || 0 }}</b></span>
                <span v-if="rule.last_fire" class="meta-dot">·</span>
                <span v-if="rule.last_fire" style="color:#3d3d3d">Last: {{ relTime(rule.last_fire) }}</span>
              </div>
            </div>
          </div>
          <div class="rule-right">
            <span :class="rule.active ? 'badge badge-active' : 'badge badge-paused'">{{ rule.active ? 'active' : 'paused' }}</span>
            <button @click="openEdit(rule)" class="btn btn-ghost btn-xs">Edit</button>
            <button @click="confirmDelete(rule)" class="btn btn-ghost btn-xs" style="color:#f87171">Delete</button>
          </div>
        </div>
      </div>
    </div>

    <!-- ── TAB: TEMPLATES ── -->
    <div v-if="activeTab === 'templates'">
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px">
        <div v-for="tpl in templates" :key="tpl.id"
          class="tpl-card">
          <div class="tpl-header">
            <div class="tpl-icon" :style="{background: tpl.color + '14', color: tpl.color}">
              <svg v-html="tpl.svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"/>
            </div>
            <span class="tpl-category">{{ tpl.category }}</span>
          </div>
          <div class="tpl-name">{{ tpl.name }}</div>
          <div class="tpl-desc">{{ tpl.desc }}</div>
          <div class="tpl-rule">
            <span class="rule-tag trigger" style="font-size:9px">IF</span>
            <span style="font-size:10px;color:#555">{{ tpl.trigger_label }}</span>
            <span class="rule-tag action" style="font-size:9px">THEN</span>
            <span style="font-size:10px;color:#555">{{ tpl.action_label }}</span>
          </div>
          <button @click="applyTemplate(tpl)" class="btn btn-secondary btn-sm" style="margin-top:12px;width:100%">
            Use Template
          </button>
        </div>
      </div>
    </div>

    <!-- ── TAB: EXECUTION LOG ── -->
    <div v-if="activeTab === 'log'">
      <div class="card">
        <div class="card-header-row">
          <span class="card-label">Execution History</span>
          <div style="display:flex;gap:8px;align-items:center">
            <div class="live-dot"/>
            <span style="font-size:11px;color:#444">Live</span>
          </div>
        </div>
        <div v-if="!execLog.length" class="empty-state">
          <div class="empty-title">No executions yet</div>
          <div class="empty-desc">Rule execution history will appear here</div>
        </div>
        <div v-else class="overflow-x-auto">
          <table class="data-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Rule</th>
                <th>Trigger</th>
                <th>Action taken</th>
                <th>Scope</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="log in execLog" :key="log.id">
                <td style="font-size:11px;color:#555;white-space:nowrap">{{ relTime(log.fired_at) }}</td>
                <td style="font-weight:500;color:#c0c0c0">{{ log.rule_name }}</td>
                <td style="font-size:11px;color:#666">{{ log.trigger_value }}</td>
                <td style="font-size:11px;color:#a0a0a0">{{ log.action_taken }}</td>
                <td style="font-size:11px;color:#555">{{ log.scope }}</td>
                <td>
                  <span :class="log.ok ? 'badge badge-active' : 'badge badge-paused'"
                    style="font-size:10px">
                    {{ log.ok ? 'applied' : 'skipped' }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ── CREATE / EDIT MODAL ── -->
    <Transition name="modal">
      <div v-if="showCreate || editTarget" class="modal-backdrop" @click.self="closeModal">
        <div class="modal-panel" style="max-width:580px">
          <div class="modal-header">
            <div class="modal-title">{{ editTarget ? 'Edit Rule' : 'New AutoPilot Rule' }}</div>
            <button @click="closeModal" class="btn btn-ghost btn-sm">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>
            </button>
          </div>
          <div class="modal-body">
            <div style="display:flex;flex-direction:column;gap:16px">

              <!-- Name -->
              <div class="form-row">
                <label class="form-label">Rule name</label>
                <input v-model="form.name" class="input" placeholder="e.g. Pause high CPA campaigns" />
              </div>

              <!-- Scope -->
              <div class="form-row">
                <label class="form-label">Apply to</label>
                <div class="chip-group">
                  <button v-for="s in scopes" :key="s"
                    @click="form.scope = s"
                    :class="['chip', form.scope === s ? 'active' : '']">
                    {{ s }}
                  </button>
                </div>
              </div>

              <!-- IF block -->
              <div class="rule-block">
                <div class="block-label trigger">IF — Trigger condition</div>
                <div style="display:grid;grid-template-columns:1fr 80px 100px;gap:8px;margin-top:8px">
                  <select v-model="form.metric" class="select">
                    <option v-for="m in metrics" :key="m.v" :value="m.v">{{ m.l }}</option>
                  </select>
                  <select v-model="form.operator" class="select">
                    <option value="gt">&gt;</option>
                    <option value="lt">&lt;</option>
                    <option value="gte">&gt;=</option>
                    <option value="lte">&lt;=</option>
                  </select>
                  <input v-model="form.threshold" type="number" class="input" placeholder="Value" />
                </div>
                <div style="margin-top:8px">
                  <select v-model="form.window" class="select" style="width:180px">
                    <option value="1h">in last 1 hour</option>
                    <option value="3h">in last 3 hours</option>
                    <option value="6h">in last 6 hours</option>
                    <option value="24h">in last 24 hours</option>
                    <option value="today">today</option>
                  </select>
                </div>
              </div>

              <!-- THEN block -->
              <div class="rule-block">
                <div class="block-label action">THEN — Action</div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px">
                  <select v-model="form.action" class="select">
                    <option v-for="a in actions" :key="a.v" :value="a.v">{{ a.l }}</option>
                  </select>
                  <input v-if="form.action !== 'pause' && form.action !== 'resume'"
                    v-model="form.action_value" type="number" class="input"
                    :placeholder="form.action.includes('budget') ? 'Amount $' : 'Percent %'" />
                </div>
              </div>

              <!-- Check interval -->
              <div class="form-row">
                <label class="form-label">Check interval</label>
                <div class="chip-group">
                  <button v-for="i in intervals" :key="i"
                    @click="form.check_interval = i"
                    :class="['chip', form.check_interval === i ? 'active' : '']">
                    {{ i }}
                  </button>
                </div>
              </div>

              <!-- Preview -->
              <div v-if="form.name && form.metric && form.threshold" class="preview-box">
                <div style="font-size:10px;color:#444;margin-bottom:6px;font-weight:600;text-transform:uppercase;letter-spacing:0.04em">Rule preview</div>
                <div style="font-size:12px;color:#888;line-height:1.7">
                  Every <b style="color:#c0c0c0">{{ form.check_interval }}</b>,
                  if <b style="color:#c0c0c0">{{ metricLabel }}</b>
                  <b style="color:#c0c0c0">{{ operatorLabel }}</b>
                  <b style="color:#c0c0c0">{{ form.threshold }}{{ metricUnit }}</b>
                  in last <b style="color:#c0c0c0">{{ form.window }}</b>
                  for <b style="color:#c0c0c0">{{ form.scope }}</b> —
                  <span style="color:#34d399">{{ actionLabel }}</span>.
                </div>
              </div>

            </div>
          </div>
          <div class="modal-footer">
            <button @click="closeModal" class="btn btn-secondary">Cancel</button>
            <button @click="saveRule"
              :disabled="saving || !form.name || !form.metric || !form.threshold"
              class="btn btn-primary">
              {{ saving ? 'Saving...' : (editTarget ? 'Save Changes' : 'Create Rule') }}
            </button>
          </div>
        </div>
      </div>
    </Transition>

    <!-- Delete confirm -->
    <Transition name="modal">
      <div v-if="deleteTarget" class="modal-backdrop" @click.self="deleteTarget = null">
        <div class="modal-panel" style="max-width:400px">
          <div class="modal-header">
            <div class="modal-title">Delete Rule</div>
          </div>
          <div class="modal-body">
            <p style="font-size:13px;color:#888;line-height:1.6">
              Delete <span style="color:#d0d0d0;font-weight:600">{{ deleteTarget?.name }}</span>? This cannot be undone.
            </p>
          </div>
          <div class="modal-footer">
            <button @click="deleteTarget = null" class="btn btn-secondary">Cancel</button>
            <button @click="deleteRule" :disabled="deleting" class="btn btn-danger">{{ deleting ? 'Deleting...' : 'Delete' }}</button>
          </div>
        </div>
      </div>
    </Transition>

  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'

const props = defineProps({ period: String })

const activeTab   = ref('rules')
const rules       = ref([])
const execLog     = ref([])
const showCreate  = ref(false)
const editTarget  = ref(null)
const deleteTarget = ref(null)
const saving      = ref(false)
const deleting    = ref(false)

const tabs = [
  { v: 'rules',     l: 'Rules' },
  { v: 'templates', l: 'Templates' },
  { v: 'log',       l: 'Execution Log' },
]

const scopes    = ['All campaigns', 'Active campaigns', 'By tag']
const intervals = ['15m', '1h', '3h', '6h', '24h']

const metrics = [
  { v: 'cpa',      l: 'CPA ($)' },
  { v: 'roi',      l: 'ROI (%)' },
  { v: 'ctr',      l: 'CTR (%)' },
  { v: 'spend',    l: 'Daily spend ($)' },
  { v: 'win_rate', l: 'Win Rate (%)' },
  { v: 'impressions', l: 'Impressions' },
  { v: 'conversions', l: 'Conversions' },
  { v: 'budget_pct',  l: 'Budget used (%)' },
]

const actions = [
  { v: 'pause',           l: 'Pause campaign' },
  { v: 'resume',          l: 'Resume campaign' },
  { v: 'increase_bid',    l: 'Increase bid by %' },
  { v: 'decrease_bid',    l: 'Decrease bid by %' },
  { v: 'increase_budget', l: 'Increase daily budget by $' },
  { v: 'decrease_budget', l: 'Decrease daily budget by $' },
  { v: 'notify',          l: 'Send notification' },
]

const emptyForm = () => ({
  name: '', scope: 'All campaigns',
  metric: 'cpa', operator: 'gt', threshold: '',
  window: '24h', action: 'pause', action_value: '',
  check_interval: '1h',
})

const form = ref(emptyForm())

const metricLabel  = computed(() => metrics.find(m => m.v === form.value.metric)?.l || '')
const operatorLabel = computed(() => ({ gt:'>',lt:'<',gte:'≥',lte:'≤' }[form.value.operator] || ''))
const metricUnit   = computed(() => ['roi','ctr','win_rate','budget_pct'].includes(form.value.metric) ? '%' : '')
const actionLabel  = computed(() => {
  const a = actions.find(a => a.v === form.value.action)
  if (!a) return ''
  if (['pause','resume','notify'].includes(form.value.action)) return a.l
  return `${a.l.split(' by ')[0]} by ${form.value.action_value || '?'}${metricUnit.value}`
})

// Stats
const todayFires  = computed(() => execLog.value.filter(l => {
  return new Date(l.fired_at) > new Date(Date.now() - 86400000)
}).length)
const savedBudget = computed(() => '$' + (todayFires.value * 12.4).toFixed(0))
const lastFire    = computed(() => {
  const l = execLog.value[0]
  return l ? relTime(l.fired_at) : null
})

// Templates
const templates = [
  {
    id: 1, name: 'Pause high CPA',
    category: 'Budget protection',
    color: '#f87171',
    svg: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    desc: 'Automatically pause campaigns when CPA exceeds target by 50%',
    trigger_label: 'CPA > $target × 1.5 in 24h',
    action_label: 'Pause campaign',
  },
  {
    id: 2, name: 'Boost winning campaigns',
    category: 'Scale',
    color: '#34d399',
    svg: '<polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/>',
    desc: 'Increase daily budget for campaigns with ROI above target',
    trigger_label: 'ROI > 200% in 24h',
    action_label: 'Increase budget by $50',
  },
  {
    id: 3, name: 'Low win rate — raise bid',
    category: 'Bid optimisation',
    color: '#60a5fa',
    svg: '<line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
    desc: 'Raise bid price when win rate drops below 1% to stay competitive',
    trigger_label: 'Win Rate < 1% in 1h',
    action_label: 'Increase bid by 20%',
  },
  {
    id: 4, name: 'Night budget safeguard',
    category: 'Budget protection',
    color: '#a78bfa',
    svg: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    desc: 'Reduce bids at night hours when conversion rate is historically low',
    trigger_label: 'Budget used > 80% by 18:00',
    action_label: 'Decrease bid by 30%',
  },
  {
    id: 5, name: 'Site overspend blocker',
    category: 'Traffic quality',
    color: '#fbbf24',
    svg: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 9h6M9 12h6M9 15h4"/>',
    desc: 'Block sites that consume >30% of budget without conversions',
    trigger_label: 'Site spend > 30% budget, conv = 0',
    action_label: 'Add to site blacklist',
  },
  {
    id: 6, name: 'Resume paused on new day',
    category: 'Automation',
    color: '#34d399',
    svg: '<polygon points="5 3 19 12 5 21 5 3"/>',
    desc: 'Automatically resume campaigns that were paused by daily budget limit',
    trigger_label: 'Campaign paused + new calendar day',
    action_label: 'Resume campaign',
  },
]

function relTime(ts) {
  const d = Date.now() - new Date(ts).getTime()
  if (d < 60000)   return Math.floor(d/1000) + 's ago'
  if (d < 3600000) return Math.floor(d/60000) + 'm ago'
  if (d < 86400000) return Math.floor(d/3600000) + 'h ago'
  return Math.floor(d/86400000) + 'd ago'
}

function openEdit(rule) {
  editTarget.value = rule
  form.value = { ...rule }
}

function closeModal() {
  showCreate.value = false
  editTarget.value = null
  form.value = emptyForm()
}

function applyTemplate(tpl) {
  form.value.name = tpl.name
  activeTab.value = 'rules'
  showCreate.value = true
}

async function loadRules() {
  try {
    const r = await fetch('/api/autopilot/rules')
    if (r.ok) rules.value = await r.json()
  } catch {}
}

async function loadLog() {
  try {
    const r = await fetch('/api/autopilot/log')
    if (r.ok) execLog.value = await r.json()
  } catch {}
}

async function toggleRule(rule) {
  await fetch(`/api/autopilot/rules/${rule.id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ active: !rule.active }),
  })
  await loadRules()
}

async function saveRule() {
  saving.value = true
  try {
    const url    = editTarget.value ? `/api/autopilot/rules/${editTarget.value.id}` : '/api/autopilot/rules'
    const method = editTarget.value ? 'PUT' : 'POST'
    const r = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form.value),
    })
    if (r.ok) { closeModal(); await loadRules() }
  } catch {}
  finally { saving.value = false }
}

const confirmDelete = r => { deleteTarget.value = r }
async function deleteRule() {
  deleting.value = true
  try {
    await fetch(`/api/autopilot/rules/${deleteTarget.value.id}`, { method: 'DELETE' })
    deleteTarget.value = null
    await loadRules()
  } catch {}
  finally { deleting.value = false }
}

onMounted(() => { loadRules(); loadLog() })
</script>

<style scoped>
.stats-strip {
  display:flex;align-items:center;
  background:#0e0e0e;border:1px solid rgba(255,255,255,0.06);
  border-radius:10px;padding:14px 20px;gap:0;
}
.stat-item { display:flex;flex-direction:column;gap:2px;padding:0 20px; }
.stat-val   { font-size:20px;font-weight:700;color:#e0e0e0;font-variant-numeric:tabular-nums; }
.stat-label { font-size:11px;color:#3d3d3d; }
.stat-divider { width:1px;height:32px;background:rgba(255,255,255,0.06); }

.tab-bar { display:flex;gap:2px;border-bottom:1px solid rgba(255,255,255,0.06); }
.tab-btn {
  padding:8px 14px;font-size:12px;color:#555;border:none;
  background:none;cursor:pointer;border-bottom:2px solid transparent;
  margin-bottom:-1px;transition:all 150ms;
}
.tab-btn:hover { color:#999; }
.tab-btn.active { color:#e0e0e0;border-bottom-color:#e0e0e0; }

.rule-card {
  display:flex;align-items:flex-start;justify-content:space-between;
  background:#0e0e0e;border:1px solid rgba(255,255,255,0.06);
  border-radius:10px;padding:14px 16px;gap:12px;
  transition:border-color 180ms;
}
.rule-card:hover { border-color:rgba(255,255,255,0.1); }
.rule-left  { display:flex;align-items:flex-start;gap:12px;flex:1;min-width:0; }
.rule-right { display:flex;align-items:center;gap:8px;flex-shrink:0; }

.toggle-btn {
  width:32px;height:18px;border-radius:9px;
  display:flex;align-items:center;padding:2px;
  transition:background 200ms;cursor:pointer;
  border:none;
}
.toggle-btn.on  { background:#34d399; justify-content:flex-end; }
.toggle-btn.off { background:rgba(255,255,255,0.1); justify-content:flex-start; }
.toggle-knob {
  width:14px;height:14px;border-radius:50%;
  background:#fff;display:block;
}

.rule-info  { display:flex;flex-direction:column;gap:5px;min-width:0; }
.rule-name  { font-size:13px;font-weight:600;color:#d0d0d0; }
.rule-logic { display:flex;align-items:center;gap:5px;flex-wrap:wrap; }
.rule-condition { font-size:11px;color:#666; }
.rule-meta  { display:flex;align-items:center;gap:5px;font-size:11px;color:#3d3d3d;flex-wrap:wrap; }
.meta-dot   { color:#2a2a2a; }

.rule-tag {
  font-size:9px;font-weight:700;padding:2px 5px;
  border-radius:3px;letter-spacing:0.04em;
}
.rule-tag.trigger { background:rgba(251,191,36,0.12);color:#fbbf24; }
.rule-tag.action  { background:rgba(52,211,153,0.12);color:#34d399; }

.rule-block {
  background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.06);
  border-radius:8px;padding:14px;
}
.block-label {
  font-size:10px;font-weight:700;letter-spacing:0.06em;
  text-transform:uppercase;
}
.block-label.trigger { color:#fbbf24; }
.block-label.action  { color:#34d399; }

.preview-box {
  background:#0a0a0a;border:1px solid rgba(255,255,255,0.07);
  border-radius:8px;padding:12px 14px;
}

.tpl-card {
  background:#0e0e0e;border:1px solid rgba(255,255,255,0.06);
  border-radius:10px;padding:16px;
  transition:border-color 180ms;
  display:flex;flex-direction:column;
}
.tpl-card:hover { border-color:rgba(255,255,255,0.1); }
.tpl-header { display:flex;align-items:center;gap:8px;margin-bottom:10px; }
.tpl-icon {
  width:28px;height:28px;border-radius:7px;
  display:flex;align-items:center;justify-content:center;
}
.tpl-category { font-size:10px;color:#444;font-weight:600;text-transform:uppercase;letter-spacing:0.04em; }
.tpl-name { font-size:13px;font-weight:600;color:#d0d0d0;margin-bottom:5px; }
.tpl-desc { font-size:11px;color:#555;line-height:1.5;flex:1; }
.tpl-rule {
  display:flex;align-items:center;gap:5px;flex-wrap:wrap;
  margin-top:10px;padding-top:10px;
  border-top:1px solid rgba(255,255,255,0.05);
}

.card-header-row {
  display:flex;align-items:center;justify-content:space-between;
  padding:14px 16px;border-bottom:1px solid rgba(255,255,255,0.05);
}
.card-label { font-size:12px;font-weight:600;color:#888; }

.live-dot {
  width:6px;height:6px;border-radius:50%;
  background:#34d399;box-shadow:0 0 6px #34d399;
  animation:pulse 2s infinite;
}
@keyframes pulse { 0%,100%{opacity:1}50%{opacity:0.4} }

.modal-backdrop {
  position:fixed;inset:0;background:rgba(0,0,0,0.7);
  backdrop-filter:blur(4px);display:flex;
  align-items:center;justify-content:center;z-index:200;padding:24px;
}
.modal-panel {
  background:#111;border:1px solid rgba(255,255,255,0.09);
  border-radius:14px;width:100%;max-width:580px;max-height:90vh;
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
