import { createApp }    from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App             from './App.vue'
import './style.css'
import { useAuth }     from './auth'

// ── Views ────────────────────────────────────────────────
import Login       from './views/Login.vue'
import Dashboard   from './views/Dashboard.vue'
import Campaigns   from './views/Campaigns.vue'
import Analytics   from './views/Analytics.vue'
import RtbBidder   from './views/RtbBidder.vue'
import Settings    from './views/Settings.vue'
import AutoPilot   from './views/AutoPilot.vue'
import PixelCenter from './views/PixelCenter.vue'
import Networks    from './views/Networks.vue'
import Creatives   from './views/Creatives.vue'
import Targeting   from './views/Targeting.vue'

// ── Router ───────────────────────────────────────────────
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login',     component: Login,       meta: { public: true, title: 'Sign In' } },
    { path: '/',          component: Dashboard,   meta: { title: 'Dashboard' } },
    { path: '/campaigns', component: Campaigns,   meta: { title: 'SmartLinks' } },
    { path: '/analytics', component: Analytics,   meta: { title: 'Analytics' } },
    { path: '/pixels',    component: PixelCenter, meta: { title: 'Pixel Center' } },
    { path: '/rtb',       component: RtbBidder,   meta: { title: 'RTB Campaigns' } },
    { path: '/creatives', component: Creatives,   meta: { title: 'Creatives' } },
    { path: '/targeting', component: Targeting,   meta: { title: 'Targeting' } },
    { path: '/autopilot', component: AutoPilot,   meta: { title: 'AutoPilot' } },
    { path: '/networks',  component: Networks,    meta: { title: 'Ad Networks' } },
    { path: '/settings',  component: Settings,    meta: { title: 'Settings' } },
  ],
})

// ── Navigation Guard ─────────────────────────────────────
router.beforeEach((to) => {
  const { isLoggedIn } = useAuth()
  if (!to.meta.public && !isLoggedIn()) {
    return { path: '/login' }
  }
  if (to.path === '/login' && isLoggedIn()) {
    return { path: '/' }
  }
})

createApp(App).use(router).mount('#app')
