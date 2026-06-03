<template>
  <div class="space-y-6">

    <div class="page-header">
      <div>
        <h1 class="page-title">Campaigns</h1>
        <p class="page-subtitle">Manage all advertising campaigns</p>
      </div>
      <button @click="showCreate = true" class="btn btn-primary">New Campaign</button>
    </div>

    <!-- Filters -->
    <div class="flex items-center gap-3">
      <input v-model="search" class="input w-64" placeholder="Search campaigns..." />
      <select v-model="filterStatus" class="select w-36">
        <option value="">All statuses</option>
        <option value="active">Active</option>
        <option value="paused">Paused</option>
        <option value="stopped">Stopped</option>
        <option value="draft">Draft</option>
      </select>
      <div class="ml-auto text-sm text-text-muted">
        {{ filtered.length }} campaign{{ filtered.length !== 1 ? 's' : '' }}
      </div>
    </div>

    <!-- Table -->
    <div class="card">
      <div v-if="loading" class="card-body space-y-2">
        <div v-for="i in 8" :key="i" class="skeleton h-10 w-full rounded" />
      </div>
      <div v-else-if="!filtered.length" class="empty-state">
        <div class="empty-icon">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
          </svg>
        </div>
        <div class="empty-title">No campaigns found</div>
        <div class="empty-desc mb-4">{{ search ? 'Try adjusting your search' : 'Create your first campaign to get started' }}</div>
        <button v-if="!search" @click="showCreate = true" class="btn btn-primary btn-sm">New Campaign</button>
      </div>
      <div v-else class="overflow-x-auto">
        <table class="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Type</th>
              <th>Status</th>
              <th>Impressions</th>
              <th>Clicks</th>
              <th>CR</th>
              <th>Spend</th>
              <th>Revenue</th>
              <th>ROI</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in filtered" :key="c.id">
              <td>
                <div class="font-medium text-text-primary">{{ c.name }}</div>
                <div class="text-xs text-text-muted font-mono">{{ c.id }}</div>
              </td>
              <td>
                <span class="badge-draft">{{ c.type || 'RTB' }}</span>
              </td>
              <td><span :class="`badge-${c.status}`">{{ c.status }}</span></td>
              <td class="tabnum">{{ fmt(c.impressions || 0) }}</td>
              <td class="tabnum">{{ fmt(c.clicks || 0) }}</td>
              <td class="tabnum" :class="(c.cr||0) > 2 ? 'val-positive' : ''">
                {{ (c.cr || 0).toFixed(2) }}%
              </td>
              <td class="tabnum text-text-secondary">${{ c.spend || '0.00' }}</td>
              <td class="tabnum val-positive">${{ c.revenue || '0.00' }}</td>
              <td class="tabnum font-semibold"
                  :class="(c.roi||0) >= 0 ? 'val-positive' : 'val-negative'">
                {{ c.roi || 0 }}%
              </td>
              <td>
                <div class="flex items-center gap-1">
                  <button class="btn btn-ghost btn-sm" @click="editCampaign(c)">Edit</button>
                  <button
                    class="btn btn-ghost btn-sm"
                    @click="toggleStatus(c)">
                    {{ c.status === 'active' ? 'Pause' : 'Resume' }}
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'

const loading      = ref(true)
const campaigns    = ref([])
const search       = ref('')
const filterStatus = ref('')
const showCreate   = ref(false)

const fmt = (n) => Number(n).toLocaleString('en-US')

const filtered = computed(() =>
  campaigns.value.filter(c => {
    const matchSearch = !search.value ||
      c.name.toLowerCase().includes(search.value.toLowerCase())
    const matchStatus = !filterStatus.value || c.status === filterStatus.value
    return matchSearch && matchStatus
  })
)

async function load() {
  loading.value = true
  try {
    const res = await fetch('/api/campaigns')
    if (res.ok) campaigns.value = await res.json()
  } catch (e) {
    console.warn('Campaigns load error:', e)
  } finally {
    loading.value = false
  }
}

async function toggleStatus(c) {
  const newStatus = c.status === 'active' ? 'paused' : 'active'
  await fetch(`/api/campaigns/${c.id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: newStatus }),
  })
  c.status = newStatus
}

function editCampaign(c) {
  // TODO: open edit modal
  console.log('Edit:', c.id)
}

onMounted(load)
</script>
