<template>
  <div class="space-y-6">

    <div class="page-header">
      <div>
        <h1 class="page-title">Settings</h1>
        <p class="page-subtitle">Account and platform configuration</p>
      </div>
    </div>

    <!-- Tabs -->
    <div class="tabs">
      <button v-for="t in tabs" :key="t.id"
        @click="activeTab = t.id"
        :class="['tab', activeTab === t.id ? 'active' : '']">
        {{ t.label }}
      </button>
    </div>

    <!-- General -->
    <div v-if="activeTab === 'general'" class="card max-w-xl">
      <div class="card-header"><span class="card-title">General</span></div>
      <div class="card-body space-y-4">
        <div>
          <label class="form-label">Platform Name</label>
          <input v-model="general.name" class="input" />
        </div>
        <div>
          <label class="form-label">Default Currency</label>
          <select v-model="general.currency" class="select">
            <option>USD</option><option>EUR</option><option>GBP</option>
          </select>
        </div>
        <div>
          <label class="form-label">Timezone</label>
          <select v-model="general.timezone" class="select">
            <option>UTC</option><option>Europe/Moscow</option>
            <option>America/New_York</option><option>Asia/Tokyo</option>
          </select>
        </div>
        <div class="flex justify-end pt-2">
          <button class="btn btn-primary">Save Changes</button>
        </div>
      </div>
    </div>

    <!-- RTB -->
    <div v-if="activeTab === 'rtb'" class="card max-w-xl">
      <div class="card-header"><span class="card-title">RTB Bidder</span></div>
      <div class="card-body space-y-4">
        <div>
          <label class="form-label">Bidder Endpoint</label>
          <input v-model="rtb.endpoint" class="input" placeholder="http://bidder:8080" />
          <p class="form-hint">The OpenRTB 2.5 endpoint SSPs should send bid requests to</p>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="form-label">Default Bid Floor ($)</label>
            <input v-model="rtb.bidFloor" type="number" step="0.001" class="input" />
          </div>
          <div>
            <label class="form-label">Max Bid Price ($)</label>
            <input v-model="rtb.maxBid" type="number" step="0.01" class="input" />
          </div>
        </div>
        <div>
          <label class="form-label">Response Timeout (ms)</label>
          <input v-model="rtb.timeout" type="number" class="input" />
          <p class="form-hint">Must be lower than SSP timeout (typically 100–150ms)</p>
        </div>
        <div>
          <label class="form-label">Campaign Refresh Interval (seconds)</label>
          <input v-model="rtb.refreshInterval" type="number" class="input" />
        </div>
        <div class="flex justify-end pt-2">
          <button class="btn btn-primary">Save Changes</button>
        </div>
      </div>
    </div>

    <!-- Notifications -->
    <div v-if="activeTab === 'notifications'" class="card max-w-xl">
      <div class="card-header"><span class="card-title">Notifications</span></div>
      <div class="card-body space-y-4">
        <div>
          <label class="form-label">Telegram Bot Token</label>
          <input v-model="notif.telegramToken" class="input" type="password"
                 placeholder="123456789:AAxxxxxx" />
        </div>
        <div>
          <label class="form-label">Telegram Chat ID</label>
          <input v-model="notif.telegramChatId" class="input" placeholder="-100xxxxxxxxx" />
        </div>
        <div class="pt-2 border-t border-surface-border">
          <label class="text-sm font-semibold mb-3 block">Alert when</label>
          <div class="space-y-3">
            <label v-for="n in notifOptions" :key="n.id"
                   class="flex items-center gap-3 cursor-pointer">
              <input type="checkbox" v-model="n.enabled"
                     class="w-4 h-4 rounded border-surface-border text-accent" />
              <span class="text-sm text-text-primary">{{ n.label }}</span>
            </label>
          </div>
        </div>
        <div class="flex justify-end pt-2">
          <button class="btn btn-primary">Save Changes</button>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref } from 'vue'

const tabs = [
  { id: 'general',       label: 'General' },
  { id: 'rtb',          label: 'RTB Bidder' },
  { id: 'notifications', label: 'Notifications' },
]
const activeTab = ref('general')

const general = ref({ name: 'Central DSP', currency: 'USD', timezone: 'UTC' })
const rtb = ref({ endpoint: 'http://bidder:8080', bidFloor: '0.01', maxBid: '5.00', timeout: 100, refreshInterval: 30 })
const notif = ref({ telegramToken: '', telegramChatId: '' })
const notifOptions = ref([
  { id: 'budget_exhausted', label: 'Campaign budget exhausted', enabled: true },
  { id: 'bidder_offline',   label: 'Bidder goes offline', enabled: true },
  { id: 'roi_negative',     label: 'ROI drops below 0%', enabled: false },
  { id: 'win_rate_low',     label: 'Win rate drops below 1%', enabled: false },
  { id: 'cpa_exceeded',     label: 'CPA exceeds target', enabled: false },
])
</script>
