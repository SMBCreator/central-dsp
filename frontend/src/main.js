import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './style.css'

// ─────────────────────────────────────────
// Routes
// ─────────────────────────────────────────
const routes = [
  { path: '/',              component: () => import('./views/Dashboard.vue'),   meta: { title: 'Dashboard' } },
  { path: '/campaigns',     component: () => import('./views/Campaigns.vue'),   meta: { title: 'Campaigns' } },
  { path: '/pixels',        component: () => import('./views/Pixels.vue'),      meta: { title: 'Pixel Center' } },
  { path: '/rtb',           component: () => import('./views/RtbBidder.vue'),   meta: { title: 'RTB Bidder' } },
  { path: '/creatives',     component: () => import('./views/Creatives.vue'),   meta: { title: 'Creatives' } },
  { path: '/targeting',     component: () => import('./views/Targeting.vue'),   meta: { title: 'Targeting' } },
  { path: '/networks',      component: () => import('./views/AdNetworks.vue'),  meta: { title: 'Ad Networks' } },
  { path: '/analytics',     component: () => import('./views/Analytics.vue'),   meta: { title: 'Analytics' } },
  { path: '/autopilot',     component: () => import('./views/AutoPilot.vue'),   meta: { title: 'AutoPilot' } },
  { path: '/settings',      component: () => import('./views/Settings.vue'),    meta: { title: 'Settings' } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// Update page title on route change
router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} — Central DSP` : 'Central DSP'
})

// ─────────────────────────────────────────
const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
