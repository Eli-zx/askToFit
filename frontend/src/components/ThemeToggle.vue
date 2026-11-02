<script setup>
import { computed } from 'vue'
import { Moon, Sunny } from '@element-plus/icons-vue'

import { useTheme } from '@/composables/useTheme'

defineProps({
  showLabel: { type: Boolean, default: false },
})

const { resolvedTheme, toggleTheme } = useTheme()
const isDark = computed(() => resolvedTheme.value === 'dark')
const actionLabel = computed(() =>
  isDark.value ? '切换到日间模式' : '切换到夜间模式',
)
</script>

<template>
  <button
    class="theme-toggle"
    type="button"
    :aria-label="actionLabel"
    :title="actionLabel"
    @click="toggleTheme"
  >
    <el-icon><Sunny v-if="isDark" /><Moon v-else /></el-icon>
    <span v-if="showLabel" class="theme-toggle__label">
      {{ isDark ? '日间' : '夜间' }}
    </span>
  </button>
</template>

<style scoped>
.theme-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 30px;
  height: 30px;
  padding: 0 7px;
  color: var(--atf-muted);
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--atf-radius-sm);
  cursor: pointer;
  gap: 6px;
  transition:
    color 0.18s ease,
    border-color 0.18s ease;
}

.theme-toggle:hover {
  color: var(--atf-ink);
  border-color: var(--atf-line-strong);
}

.theme-toggle:focus-visible {
  outline: 1px solid var(--atf-brand);
  outline-offset: 2px;
}

.theme-toggle .el-icon {
  font-size: 15px;
}

.theme-toggle__label {
  font-size: 10.5px;
  letter-spacing: 0.08em;
}
</style>
