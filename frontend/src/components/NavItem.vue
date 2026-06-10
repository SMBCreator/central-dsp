<template>
  <RouterLink :to="to" :class="['nav-item', { active: isActive }]" :title="label">
    <i :data-lucide="icon" class="nav-icon" />
    <span class="nav-label">{{ label }}</span>
  </RouterLink>
</template>

<script setup>
import { computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { createIcons, icons } from 'lucide'

const props   = defineProps({ to: String, icon: String, label: String })
const route   = useRoute()
const isActive = computed(() => {
  if (props.to === '/') return route.path === '/'
  return route.path.startsWith(props.to)
})

const refresh = () => createIcons({ icons })
onMounted(refresh)
watch(() => props.icon, refresh)
</script>

<style scoped>
.nav-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 7px 10px;
  border-radius: 7px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-muted);
  text-decoration: none;
  transition: background 130ms, color 130ms;
  cursor: pointer;
}
.nav-item:hover {
  background: var(--hover-bg);
  color: var(--text);
}
.nav-item.active {
  background: var(--active-bg);
  color: var(--active-text);
}
.nav-icon {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  stroke-width: 1.8px;
}
.nav-label { flex: 1; }
</style>
