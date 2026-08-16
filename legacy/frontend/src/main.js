import { createApp }    from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App             from './App.vue'
import './style.css'

// ── Views ────────────────────────────────────────────────
import Dashboard  from './views/Dashboard.vue'
import Campaigns  from './views/Campaigns.vue'
import Analytics  from './views/Analytics.vue'
import RtbBidder  from './views/RtbBidder.vue'
import Settings   from './views/Settings.vue'

// Lazy-loaded views (not yet implemented — show placeholder)
const Placeholder = {
  template: `
    <div class="empty-state">
      <div class="empty-icon">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="1.5">
          <circle cx="12" cy="12" r="9"/>
          <path d="M12 8v4l3 3"/>
        </svg>
      </div>
      <div class="empty-title">Coming soon</div>
      <div class="empty-desc">This section is under active development</div>
    </div>
  `
}

// ── Router ───────────────────────────────────────────────
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/',          component: Dashboard,  meta: { title: 'Dashboard' } },
    { path: '/campaigns', component: Campaigns,  meta: { title: 'Campaigns' } },
    { path: '/analytics', component: Analytics,  meta: { title: 'Analytics' } },
    { path: '/pixels',    component: Placeholder, meta: { title: 'Pixel Center' } },
    { path: '/rtb',       component: RtbBidder,  meta: { title: 'RTB Bidder' } },
    { path: '/creatives', component: Placeholder, meta: { title: 'Creatives' } },
    { path: '/targeting', component: Placeholder, meta: { title: 'Targeting' } },
    { path: '/autopilot', component: Placeholder, meta: { title: 'AutoPilot' } },
    { path: '/networks',  component: Placeholder, meta: { title: 'Ad Networks' } },
    { path: '/settings',  component: Settings,   meta: { title: 'Settings' } },
  ],
})

createApp(App).use(router).mount('#app')
