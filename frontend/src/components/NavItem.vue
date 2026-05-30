<template>
  <RouterLink
    :to="to"
    class="flex items-center gap-2.5 px-3 py-2 rounded text-sm transition-all duration-150
           text-[#8b949e] hover:text-gray-100 hover:bg-[#21262d]"
    :class="{ 'bg-[#21262d] !text-[#00d4ff]': isActive }"
  >
    <span class="text-base w-5 text-center">{{ icon }}</span>
    <span>{{ label }}</span>
  </RouterLink>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const props = defineProps({
  to:    { type: String, required: true },
  icon:  { type: String, required: true },
  label: { type: String, required: true },
})

const route = useRoute()
const isActive = computed(() =>
  props.to === '/' ? route.path === '/' : route.path.startsWith(props.to)
)
</script>
