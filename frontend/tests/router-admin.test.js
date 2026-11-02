import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it } from 'vitest'

import { authGuard } from '@/router'
import { useAuthStore } from '@/stores/auth'

const Empty = { template: '<div />' }

function createTestRouter() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Empty },
      { path: '/login', name: 'login', component: Empty, meta: { guestOnly: true } },
      { path: '/chat', name: 'chat', component: Empty, meta: { requiresAuth: true } },
      {
        path: '/knowledge',
        name: 'knowledge',
        component: Empty,
        meta: { requiresAuth: true, requiresAdmin: true },
      },
      {
        path: '/knowledge-gaps',
        name: 'knowledge-gaps',
        component: Empty,
        meta: { requiresAuth: true, requiresAdmin: true },
      },
    ],
  })
  router.beforeEach(authGuard)
  return router
}

function signInAs(isAdmin) {
  const auth = useAuthStore()
  auth.token = 'test-token'
  auth.user = { username: isAdmin ? 'alice' : 'bob', is_admin: isAdmin }
}

describe('管理路由权限', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('普通用户直接访问知识库或缺口运营页会被重定向到对话页', async () => {
    const router = createTestRouter()
    signInAs(false)

    await router.push('/knowledge')
    expect(router.currentRoute.value.name).toBe('chat')

    await router.push('/knowledge-gaps')
    expect(router.currentRoute.value.name).toBe('chat')
  })

  it('管理员可以直接访问知识库和缺口运营页', async () => {
    const router = createTestRouter()
    signInAs(true)

    await router.push('/knowledge')
    expect(router.currentRoute.value.name).toBe('knowledge')

    await router.push('/knowledge-gaps')
    expect(router.currentRoute.value.name).toBe('knowledge-gaps')
  })
})
