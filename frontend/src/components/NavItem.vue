<template>
  <RouterLink :to="to" class="nav-item" :class="{ active: isActive }">
    <span class="nav-label">{{ label }}</span>
  </RouterLink>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const props = defineProps({ to: String, label: String, icon: String })
const route = useRoute()
const isActive = computed(() =>
  props.to === '/' ? route.path === '/' : route.path.startsWith(props.to)
)
</script>

<style scoped>
.nav-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 7px;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--text-muted);
  text-decoration: none;
  transition: background 130ms, color 130ms;
  cursor: pointer;
  margin-bottom: 1px;
}
.nav-item:hover {
  background: var(--hover-bg);
  color: var(--text);
}
.nav-item.active {
  background: var(--active-bg);
  color: var(--active-text);
}
.nav-label { flex:1; }
</style>
