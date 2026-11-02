import { computed, ref } from 'vue'

export const THEME_STORAGE_KEY = 'asktofit.theme'
export const THEME_LIGHT = 'light'
export const THEME_DARK = 'dark'
export const THEME_SYSTEM = 'system'

const THEME_COLOR = {
  light: '#f2eee6',
  dark: '#0b0b0a',
}

function getMediaQuery() {
  if (typeof window === 'undefined' || !window.matchMedia) return null
  return window.matchMedia('(prefers-color-scheme: dark)')
}

function readPreference() {
  if (typeof localStorage === 'undefined') return THEME_SYSTEM
  try {
    const value = localStorage.getItem(THEME_STORAGE_KEY)
    if ([THEME_LIGHT, THEME_DARK, THEME_SYSTEM].includes(value)) return value
  } catch {
    // 隐私模式下 localStorage 可能不可用，回退到跟随系统
  }
  return THEME_SYSTEM
}

const mediaQuery = getMediaQuery()
const preference = ref(readPreference())
const systemDark = ref(Boolean(mediaQuery?.matches))
const resolvedTheme = computed(() => {
  if (preference.value === THEME_SYSTEM) {
    return systemDark.value ? THEME_DARK : THEME_LIGHT
  }
  return preference.value
})
let listening = false

export function applyTheme(theme = resolvedTheme.value) {
  if (typeof document === 'undefined') return
  const isDark = theme === THEME_DARK
  const root = document.documentElement
  root.dataset.theme = isDark ? THEME_DARK : THEME_LIGHT
  // Element Plus 的暗色变量挂在 html.dark 上
  root.classList.toggle('dark', isDark)
  root.style.colorScheme = isDark ? THEME_DARK : THEME_LIGHT

  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) {
    meta.setAttribute('content', isDark ? THEME_COLOR.dark : THEME_COLOR.light)
  }
}

export function setTheme(value) {
  const next = [THEME_LIGHT, THEME_DARK, THEME_SYSTEM].includes(value)
    ? value
    : THEME_SYSTEM
  preference.value = next
  try {
    localStorage.setItem(THEME_STORAGE_KEY, next)
  } catch {
    // 忽略存储失败，本次会话仍然生效
  }
  applyTheme()
}

export function toggleTheme() {
  setTheme(resolvedTheme.value === THEME_DARK ? THEME_LIGHT : THEME_DARK)
}

function onSystemThemeChange(event) {
  systemDark.value = event.matches
  if (preference.value === THEME_SYSTEM) applyTheme()
}

/**
 * 在应用挂载前调用：同步首屏主题，并监听系统主题变化。
 * index.html 的内联脚本已先做过一次，这里只负责后续同步。
 */
export function initTheme() {
  if (mediaQuery && !listening) {
    mediaQuery.addEventListener?.('change', onSystemThemeChange)
    listening = true
  }
  applyTheme()
}

export function useTheme() {
  return {
    preference,
    resolvedTheme,
    setTheme,
    toggleTheme,
    initTheme,
  }
}
