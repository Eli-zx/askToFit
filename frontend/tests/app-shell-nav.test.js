import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import AppShell from '@/components/AppShell.vue'
import { useAuthStore } from '@/stores/auth'

const Empty = { template: '<div />' }

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Empty },
      { path: '/chat', component: Empty },
      { path: '/knowledge', component: Empty },
      { path: '/knowledge-gaps', component: Empty },
      { path: '/evaluation', component: Empty },
    ],
  })
}

async function renderShell(user) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.token = 'test-token'
  auth.user = user

  const router = createTestRouter()
  await router.push('/chat')
  await router.isReady()

  return mount(AppShell, {
    global: {
      plugins: [pinia, router],
      stubs: {
        ElIcon: { template: '<span><slot /></span>' },
        ElDropdown: { template: '<span><slot /></span>' },
        ElDropdownMenu: { template: '<span><slot /></span>' },
        ElDropdownItem: { template: '<span><slot /></span>' },
      },
    },
  })
}

describe('管理导航可见性', () => {
  it('普通用户看不到知识库和缺口运营入口', async () => {
    const wrapper = await renderShell({ username: 'bob', is_admin: false })
    const nav = wrapper.find('.shell__nav').text()

    expect(nav).not.toContain('知识库')
    expect(nav).not.toContain('缺口运营')
  })

  it('管理员可以看到知识库和缺口运营入口', async () => {
    const wrapper = await renderShell({ username: 'alice', is_admin: true })
    const nav = wrapper.find('.shell__nav').text()

    expect(nav).toContain('知识库')
    expect(nav).toContain('缺口运营')
  })
})
