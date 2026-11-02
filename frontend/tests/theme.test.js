import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const STORAGE_KEY = 'asktofit.theme'
let changeHandler = null

function mockMatchMedia(matches) {
  const listeners = []
  window.matchMedia = vi.fn(() => ({
    matches,
    addEventListener: (event, handler) => {
      if (event === 'change') {
        listeners.push(handler)
        changeHandler = handler
      }
    },
  }))
  return listeners
}

async function loadTheme() {
  vi.resetModules()
  return import('@/composables/useTheme')
}

describe('主题切换', () => {
  beforeEach(() => {
    localStorage.clear()
    changeHandler = null
    document.documentElement.className = ''
    document.documentElement.removeAttribute('data-theme')
    const meta = document.createElement('meta')
    meta.setAttribute('name', 'theme-color')
    meta.setAttribute('content', '#f2eee6')
    document.head.appendChild(meta)
    mockMatchMedia(false)
  })

  afterEach(() => {
    document.querySelector('meta[name="theme-color"]')?.remove()
    vi.restoreAllMocks()
  })

  it('未设置偏好时跟随系统亮色', async () => {
    const { useTheme } = await loadTheme()
    const { initTheme, resolvedTheme } = useTheme()
    initTheme()

    expect(resolvedTheme.value).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })

  it('系统暗色时首屏即应用 dark class', async () => {
    mockMatchMedia(true)
    const { useTheme } = await loadTheme()
    const { initTheme, resolvedTheme } = useTheme()
    initTheme()

    expect(resolvedTheme.value).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(
      document.querySelector('meta[name="theme-color"]').getAttribute('content'),
    ).toBe('#0b0b0a')
  })

  it('localStorage 偏好优先于系统设置', async () => {
    mockMatchMedia(true)
    localStorage.setItem(STORAGE_KEY, 'light')
    const { useTheme } = await loadTheme()
    const { initTheme, resolvedTheme } = useTheme()
    initTheme()

    expect(resolvedTheme.value).toBe('light')
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })

  it('toggleTheme 在明暗之间切换并持久化', async () => {
    const { useTheme } = await loadTheme()
    const { initTheme, setTheme, toggleTheme, resolvedTheme } = useTheme()
    initTheme()

    toggleTheme()
    expect(resolvedTheme.value).toBe('dark')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)

    toggleTheme()
    expect(resolvedTheme.value).toBe('light')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('light')

    setTheme('system')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('system')
    expect(resolvedTheme.value).toBe('light')
  })

  it('system 模式下监听系统主题变化', async () => {
    const { useTheme } = await loadTheme()
    const { initTheme, resolvedTheme } = useTheme()
    initTheme()

    expect(typeof changeHandler).toBe('function')
    changeHandler({ matches: true })
    // 响应式更新是同步的
    expect(resolvedTheme.value).toBe('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })
})
