import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { guestOnly: true, title: '登录' },
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('@/views/RegisterView.vue'),
    meta: { guestOnly: true, title: '注册' },
  },
  {
    path: '/',
    component: () => import('@/components/AppShell.vue'),
    meta: { requiresAuth: true },
    children: [
      { path: '', redirect: '/chat' },
      {
        path: 'chat/:id?',
        name: 'chat',
        component: () => import('@/views/ChatView.vue'),
        props: true,
        meta: { title: '智能对话' },
      },
      {
        path: 'knowledge',
        name: 'knowledge',
        component: () => import('@/views/KnowledgeView.vue'),
        meta: { title: '知识库管理' },
      },
      {
        path: 'knowledge/:id',
        name: 'knowledge-detail',
        component: () => import('@/views/KnowledgeDetailView.vue'),
        props: true,
        meta: { title: '知识库详情' },
      },
      {
        path: 'knowledge-gaps',
        name: 'knowledge-gaps',
        component: () => import('@/views/KnowledgeGapView.vue'),
        meta: { title: '知识缺口运营' },
      },
      {
        path: 'evaluation',
        name: 'evaluation',
        component: () => import('@/views/EvaluationView.vue'),
        meta: { title: '评估看板', requiresAdmin: true },
      },
      {
        path: 'documents/:id/chunks',
        name: 'chunk-preview',
        component: () => import('@/views/ChunkPreviewView.vue'),
        props: true,
        meta: { title: '分块预览' },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { title: '页面不存在' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()

  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return {
      name: 'login',
      query: { redirect: to.fullPath },
    }
  }

  if (to.meta.guestOnly && auth.isAuthenticated) {
    return { name: 'chat' }
  }

  if (auth.isAuthenticated && !auth.user) {
    try {
      await auth.loadProfile()
    } catch {
      // 401 由 http 拦截器统一处理
    }
  }

  if (to.meta.requiresAdmin && !auth.user?.is_admin) {
    return { name: 'chat' }
  }

  document.title = to.meta.title
    ? `${to.meta.title} · AskToFit`
    : 'AskToFit · 售前智能导购'
  return true
})

export default router
