import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const TOKEN_KEY = 'asktofit.token'
const USER_KEY = 'asktofit.user'
const originalLocation = window.location

function stubLocation(pathname = '/chat/1', search = '') {
  const replace = vi.fn()
  Object.defineProperty(window, 'location', {
    configurable: true,
    writable: true,
    value: { pathname, search, replace },
  })
  return replace
}

async function loadAuthModule() {
  vi.resetModules()
  return import('@/api/http')
}

describe('redirectToLoginOnce', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    Object.defineProperty(window, 'location', {
      configurable: true,
      writable: true,
      value: originalLocation,
    })
    vi.restoreAllMocks()
  })

  it('有 token 时清理会话并只跳转一次', async () => {
    const replace = stubLocation('/chat/9', '?tab=1')
    localStorage.setItem(TOKEN_KEY, 'token')
    localStorage.setItem(USER_KEY, '{"id":1}')

    const { redirectToLoginOnce } = await loadAuthModule()
    redirectToLoginOnce()
    redirectToLoginOnce()

    expect(replace).toHaveBeenCalledTimes(1)
    expect(replace).toHaveBeenCalledWith(
      `/login?redirect=${encodeURIComponent('/chat/9?tab=1')}`,
    )
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull()
    expect(localStorage.getItem(USER_KEY)).toBeNull()
  })

  it('没有 token 时不整页跳转，交给路由守卫', async () => {
    const replace = stubLocation('/chat/9')

    const { redirectToLoginOnce } = await loadAuthModule()
    redirectToLoginOnce()

    expect(replace).not.toHaveBeenCalled()
  })

  it('已经在登录页时不再跳转', async () => {
    const replace = stubLocation('/login', '?redirect=%2Fchat')
    localStorage.setItem(TOKEN_KEY, 'token')

    const { redirectToLoginOnce } = await loadAuthModule()
    redirectToLoginOnce()

    expect(replace).not.toHaveBeenCalled()
  })
})
