<template>
  <RouterLink
    :to="to"
    :class="[
      'flex items-center gap-2.5 px-3 py-2 rounded-md text-sm font-medium',
      'transition-all duration-100 ease-smooth',
      isActive
        ? 'bg-brand text-white'
        : 'text-text-secondary hover:bg-surface-hover hover:text-text-primary'
    ]"
  >
    <span>{{ label }}</span>
  </RouterLink>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const props = defineProps({
  to:    { type: String, required: true },
  label: { type: String, required: true },
})

const route = useRoute()
const isActive = computed(() =>
  props.to === '/'
    ? route.path === '/'
    : route.path.startsWith(props.to)
)
</script>
