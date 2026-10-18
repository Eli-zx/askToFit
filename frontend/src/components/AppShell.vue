<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowDown,
  ChatDotRound,
  Collection,
  DataAnalysis,
  SwitchButton,
  TrendCharts,
} from '@element-plus/icons-vue'

import BrandMark from '@/components/BrandMark.vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const activeNav = computed(() => {
  if (route.path.startsWith('/evaluation')) {
    return 'evaluation'
  }
  if (route.path.startsWith('/knowledge-gaps')) {
    return 'knowledge-gaps'
  }
  if (route.path.startsWith('/knowledge') || route.path.startsWith('/documents')) {
    return 'knowledge'
  }
  return 'chat'
})

function handleCommand(command) {
  if (command === 'logout') {
    auth.signOut()
    router.push({ name: 'login' })
  }
}
</script>

<template>
  <div class="shell">
    <div class="atf-aurora" />
    <header class="shell__header">
      <div class="shell__header-inner">
        <RouterLink class="shell__brand" to="/chat" aria-label="AskToFit 首页">
          <BrandMark />
        </RouterLink>

        <nav class="shell__nav" aria-label="主导航">
          <RouterLink
            class="shell__nav-item"
            :class="{ 'is-active': activeNav === 'chat' }"
            to="/chat"
          >
            <el-icon><ChatDotRound /></el-icon>
            <span>智能对话</span>
          </RouterLink>
          <RouterLink
            class="shell__nav-item"
            :class="{ 'is-active': activeNav === 'knowledge' }"
            to="/knowledge"
          >
            <el-icon><Collection /></el-icon>
            <span>知识库</span>
          </RouterLink>
          <RouterLink
            class="shell__nav-item"
            :class="{ 'is-active': activeNav === 'knowledge-gaps' }"
            to="/knowledge-gaps"
          >
            <el-icon><DataAnalysis /></el-icon>
            <span>缺口运营</span>
          </RouterLink>
          <RouterLink
            v-if="auth.user?.is_admin"
            class="shell__nav-item"
            :class="{ 'is-active': activeNav === 'evaluation' }"
            to="/evaluation"
          >
            <el-icon><TrendCharts /></el-icon>
            <span>评估看板</span>
          </RouterLink>
        </nav>

        <div class="shell__tools">
          <span class="shell__status">
            <i />
            RAG 在线
          </span>
          <el-dropdown trigger="click" @command="handleCommand">
            <button class="shell__user" type="button">
              <span class="shell__avatar">{{ auth.initials }}</span>
              <span class="shell__name">{{ auth.displayName }}</span>
              <el-icon class="shell__caret"><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item disabled>
                  {{ auth.user?.username || '当前用户' }}
                </el-dropdown-item>
                <el-dropdown-item divided command="logout">
                  <el-icon><SwitchButton /></el-icon>
                  退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>
    </header>

    <main class="shell__main">
      <RouterView v-slot="{ Component, route: childRoute }">
        <Transition name="page-fade" mode="out-in">
          <component :is="Component" :key="childRoute.name" />
        </Transition>
      </RouterView>
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.shell__header {
  position: fixed;
  top: 0;
  right: 0;
  left: 0;
  z-index: 40;
  height: var(--atf-header-height);
  background: rgba(250, 249, 255, 0.78);
  border-bottom: 1px solid rgba(23, 24, 47, 0.06);
  backdrop-filter: blur(18px);
}

.shell__header-inner {
  display: flex;
  align-items: center;
  width: min(1400px, 100%);
  height: 100%;
  padding: 0 28px;
  margin: 0 auto;
  gap: 34px;
}

.shell__brand {
  flex: none;
}

.shell__nav {
  display: flex;
  padding: 5px;
  background: rgba(91, 91, 214, 0.06);
  border-radius: 999px;
  gap: 3px;
}

.shell__nav-item {
  display: inline-flex;
  align-items: center;
  padding: 8px 18px;
  color: var(--atf-muted);
  font-size: 13.5px;
  font-weight: 650;
  border-radius: 999px;
  transition: all 0.25s cubic-bezier(0.22, 1, 0.36, 1);
  gap: 7px;
}

.shell__nav-item:hover {
  color: var(--atf-brand);
}

.shell__nav-item.is-active {
  color: var(--atf-brand-deep);
  background: #fff;
  box-shadow: 0 4px 14px rgba(43, 38, 95, 0.09);
}

.shell__nav-item .el-icon {
  font-size: 16px;
}

.shell__tools {
  display: flex;
  align-items: center;
  margin-left: auto;
  gap: 18px;
}

.shell__status {
  display: inline-flex;
  align-items: center;
  color: #2f8f73;
  font-size: 12px;
  font-weight: 650;
  gap: 7px;
}

.shell__status i {
  width: 7px;
  height: 7px;
  background: #2dd4a7;
  border-radius: 50%;
  box-shadow: 0 0 0 4px rgba(45, 212, 167, 0.16);
  animation: atf-pulse 2.4s ease-in-out infinite;
}

.shell__user {
  display: inline-flex;
  align-items: center;
  padding: 5px 10px 5px 5px;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.2s ease;
  gap: 8px;
}

.shell__user:hover {
  border-color: rgba(91, 91, 214, 0.3);
  box-shadow: var(--atf-shadow-sm);
}

.shell__avatar {
  display: grid;
  width: 30px;
  height: 30px;
  color: #fff;
  font-size: 13px;
  font-weight: 750;
  background: linear-gradient(135deg, #5b5bd6, #8b7cf6);
  border-radius: 50%;
  place-items: center;
}

.shell__name {
  max-width: 110px;
  overflow: hidden;
  color: var(--atf-ink-soft);
  font-size: 13px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shell__caret {
  color: var(--atf-muted);
  font-size: 12px;
}

.shell__main {
  flex: 1;
  min-width: 0;
}

@media (max-width: 720px) {
  .shell__header-inner {
    padding: 0 14px;
    gap: 14px;
  }

  .shell__nav-item {
    padding: 7px 12px;
  }

  .shell__status,
  .shell__name {
    display: none;
  }

  .shell__tools {
    gap: 8px;
  }
}
</style>
