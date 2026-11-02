<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowDown, SwitchButton } from '@element-plus/icons-vue'

import BrandMark from '@/components/BrandMark.vue'
import ThemeToggle from '@/components/ThemeToggle.vue'
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
            <span class="shell__nav-index">01</span>
            <span>智能对话</span>
          </RouterLink>
          <RouterLink
            v-if="auth.user?.is_admin"
            class="shell__nav-item"
            :class="{ 'is-active': activeNav === 'knowledge' }"
            to="/knowledge"
          >
            <span class="shell__nav-index">02</span>
            <span>知识库</span>
          </RouterLink>
          <RouterLink
            v-if="auth.user?.is_admin"
            class="shell__nav-item"
            :class="{ 'is-active': activeNav === 'knowledge-gaps' }"
            to="/knowledge-gaps"
          >
            <span class="shell__nav-index">03</span>
            <span>缺口运营</span>
          </RouterLink>
          <RouterLink
            v-if="auth.user?.is_admin"
            class="shell__nav-item"
            :class="{ 'is-active': activeNav === 'evaluation' }"
            to="/evaluation"
          >
            <span class="shell__nav-index">04</span>
            <span>评估看板</span>
          </RouterLink>
        </nav>

        <div class="shell__tools">
          <span class="shell__status">
            <i />
            RAG 在线
          </span>
          <ThemeToggle />
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

/* 报头：纸色底 + 两条细线，不做毛玻璃与胶囊容器 */
.shell__header {
  position: fixed;
  top: 0;
  right: 0;
  left: 0;
  z-index: 40;
  height: var(--atf-header-height);
  background: var(--atf-bg);
  border-bottom: 1px solid var(--atf-line-strong);
}

.shell__header::after {
  position: absolute;
  right: 0;
  bottom: -4px;
  left: 0;
  height: 1px;
  content: '';
  background: var(--atf-line);
}

.shell__header-inner {
  display: flex;
  align-items: center;
  width: min(1440px, 100%);
  height: 100%;
  padding: 0 30px;
  margin: 0 auto;
  gap: 42px;
}

.shell__brand {
  flex: none;
}

.shell__nav {
  display: flex;
  align-self: stretch;
  gap: 30px;
}

.shell__nav-item {
  position: relative;
  display: inline-flex;
  align-items: center;
  padding: 0 1px;
  color: var(--atf-muted);
  font-size: 13px;
  font-weight: 500;
  letter-spacing: 0.02em;
  transition: color 0.18s ease;
  gap: 7px;
}

.shell__nav-item::after {
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  height: 2px;
  content: '';
  background: transparent;
  transition: background 0.18s ease;
}

.shell__nav-item:hover {
  color: var(--atf-ink);
}

.shell__nav-item.is-active {
  color: var(--atf-ink);
  font-weight: 650;
}

.shell__nav-item.is-active::after {
  background: var(--atf-brand);
}

.shell__nav-index {
  color: var(--atf-brand);
  font-family: var(--atf-font-mono);
  font-size: 8.5px;
  letter-spacing: 0.06em;
  transform: translateY(-4px);
}

.shell__tools {
  display: flex;
  align-items: center;
  margin-left: auto;
  gap: 22px;
}

.shell__status {
  display: inline-flex;
  align-items: center;
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 9.5px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  gap: 7px;
}

/* 在线状态用一小段铜色横线，不用通用 SaaS 的绿色圆点 */
.shell__status i {
  width: 10px;
  height: 1px;
  background: var(--atf-bronze);
  border-radius: 0;
}

.shell__user {
  display: inline-flex;
  align-items: center;
  padding: 3px 0 3px 3px;
  background: transparent;
  border: 0;
  cursor: pointer;
  gap: 8px;
}

.shell__avatar {
  display: grid;
  width: 28px;
  height: 28px;
  color: var(--atf-bg);
  font-family: var(--atf-font-display);
  font-size: 12px;
  font-weight: 600;
  background: var(--atf-ink);
  border-radius: 1px;
  place-items: center;
}

.shell__user:hover .shell__avatar {
  background: var(--atf-brand);
}

.shell__name {
  max-width: 110px;
  overflow: hidden;
  color: var(--atf-ink-soft);
  font-size: 12px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shell__caret {
  color: var(--atf-muted);
  font-size: 11px;
}

.shell__main {
  flex: 1;
  min-width: 0;
}

@media (max-width: 900px) {
  .shell__header-inner {
    display: grid;
    grid-template-columns: auto 1fr auto;
    grid-template-rows: 1fr 36px;
    padding: 0 14px;
    gap: 0 14px;
  }

  .shell__brand {
    grid-column: 1;
    grid-row: 1;
  }

  .shell__nav {
    grid-column: 1 / -1;
    grid-row: 2;
    justify-content: space-between;
    align-self: stretch;
    gap: 0;
    border-top: 1px solid var(--atf-line);
  }

  .shell__nav-item {
    flex: 1;
    justify-content: center;
    font-size: 11.5px;
    letter-spacing: 0.01em;
    gap: 5px;
  }

  .shell__nav-item::after {
    right: 6px;
    left: 6px;
  }

  .shell__nav-index {
    font-size: 7.5px;
    transform: translateY(-3px);
  }

  .shell__status,
  .shell__name {
    display: none;
  }

  .shell__tools {
    grid-column: 3;
    grid-row: 1;
    margin-left: 0;
    justify-self: end;
    gap: 8px;
  }
}
</style>
