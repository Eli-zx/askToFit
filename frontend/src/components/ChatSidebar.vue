<script setup>
import { computed, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import { Box, Delete, EditPen, MoreFilled, Plus, Search } from '@element-plus/icons-vue'

import { formatRelativeTime, truncate } from '@/utils/format'

const props = defineProps({
  conversations: { type: Array, default: () => [] },
  activeId: { type: [Number, String], default: null },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['select', 'create', 'rename', 'delete', 'archive', 'restore'])

const keyword = ref('')
const showArchived = ref(false)

const activeConversations = computed(() =>
  props.conversations.filter((item) => !item.archived_at),
)
const archivedConversations = computed(() =>
  props.conversations.filter((item) => item.archived_at),
)

const filtered = computed(() => {
  const source = showArchived.value
    ? [...activeConversations.value, ...archivedConversations.value]
    : activeConversations.value
  const text = keyword.value.trim().toLowerCase()
  if (!text) return source
  return source.filter((item) =>
    `${item.title} ${item.last_message || ''}`.toLowerCase().includes(text),
  )
})

function handleCommand(command, conversation) {
  if (command === 'rename') {
    ElMessageBox.prompt('输入新的会话名称', '重命名会话', {
      inputValue: conversation.title,
      inputPattern: /\S+/,
      inputErrorMessage: '名称不能为空',
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    })
      .then(({ value }) => emit('rename', conversation, value.trim()))
      .catch(() => {})
  } else if (command === 'archive') {
    ElMessageBox.confirm(
      `归档「${truncate(conversation.title, 20)}」？归档后仍可在“已归档”中找回。`,
      '归档会话',
      {
        type: 'info',
        confirmButtonText: '归档',
        cancelButtonText: '取消',
      },
    )
      .then(() => emit('archive', conversation))
      .catch(() => {})
  } else if (command === 'restore') {
    emit('restore', conversation)
  } else if (command === 'delete') {
    ElMessageBox.confirm(
      `删除「${truncate(conversation.title, 20)}」以及全部消息记录？该操作不可恢复。`,
      '删除会话',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      },
    )
      .then(() => emit('delete', conversation))
      .catch(() => {})
  }
}
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar__head">
      <el-button class="sidebar__new" type="primary" @click="emit('create')">
        <el-icon><Plus /></el-icon>
        新建对话
      </el-button>
    </div>

    <div class="sidebar__search">
      <el-input v-model="keyword" placeholder="搜索会话" clearable>
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>
    </div>

    <div class="sidebar__label">
      <span>最近对话</span>
      <small>{{ activeConversations.length }}</small>
    </div>

    <button
      v-if="archivedConversations.length"
      type="button"
      class="sidebar__archive-toggle"
      :class="{ 'is-open': showArchived }"
      @click="showArchived = !showArchived"
    >
      <el-icon><Box /></el-icon>
      {{ showArchived ? '隐藏已归档' : '查看已归档' }}
      <span>{{ archivedConversations.length }}</span>
    </button>

    <div class="sidebar__list atf-scrollbar">
      <div v-if="loading" class="sidebar__skeleton">
        <el-skeleton :rows="4" animated />
      </div>

      <template v-else>
        <button
          v-for="conversation in filtered"
          :key="conversation.id"
          class="sidebar__item"
          :class="{
            'is-active': String(conversation.id) === String(activeId),
            'is-archived': Boolean(conversation.archived_at),
          }"
          type="button"
          @click="emit('select', conversation)"
        >
          <span class="sidebar__item-main">
            <span class="sidebar__item-title">{{ conversation.title }}</span>
            <span class="sidebar__item-preview">
              {{ conversation.last_message || '开始你的第一个问题…' }}
            </span>
          </span>
          <span class="sidebar__item-side">
            <span class="sidebar__item-time">
              {{ formatRelativeTime(conversation.updated_at) }}
            </span>
            <el-dropdown
              trigger="click"
              @command="handleCommand($event, conversation)"
              @click.stop
            >
              <span class="sidebar__item-menu" @click.stop>
                <el-icon><MoreFilled /></el-icon>
              </span>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="rename">
                    <el-icon><EditPen /></el-icon>
                    重命名
                  </el-dropdown-item>
                  <el-dropdown-item
                    v-if="!conversation.archived_at"
                    command="archive"
                  >
                    <el-icon><Box /></el-icon>
                    归档
                  </el-dropdown-item>
                  <el-dropdown-item v-else command="restore">
                    <el-icon><Box /></el-icon>
                    取消归档
                  </el-dropdown-item>
                  <el-dropdown-item command="delete" divided>
                    <el-icon><Delete /></el-icon>
                    删除会话
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </span>
          <i class="sidebar__active-bar" />
        </button>

        <div v-if="!filtered.length" class="sidebar__empty">
          <span>{{ keyword ? '没有匹配的会话' : '还没有对话记录' }}</span>
          <small>{{ keyword ? '换个关键词试试' : '点击上方按钮开启咨询' }}</small>
        </div>
      </template>
    </div>

    <div class="sidebar__footer">
      <div class="sidebar__footer-icon">
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path
            d="M12 3.5c.8 3.2 2.4 4.8 5.6 5.6-3.2.8-4.8 2.4-5.6 5.6-.8-3.2-2.4-4.8-5.6-5.6 3.2-.8 4.8-2.4 5.6-5.6Z"
            fill="currentColor"
          />
          <path d="M18 15.5l.7 2.3 2.3.7-2.3.7-.7 2.3-.7-2.3-2.3-.7 2.3-.7.7-2.3Z" fill="currentColor" opacity=".5" />
        </svg>
      </div>
      <p>回答由知识库检索增强，重要政策请以引用资料为准。</p>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  width: 292px;
  min-width: 292px;
  height: 100%;
  padding: 20px 14px 16px;
  overflow: hidden;
  background: rgba(251, 250, 255, 0.72);
  border-right: 1px solid var(--atf-line);
  backdrop-filter: blur(14px);
}

.sidebar__head {
  padding: 0 4px;
}

.sidebar__new {
  width: 100%;
  height: 42px;
  font-size: 13px;
  border-radius: 12px;
}

.sidebar__search {
  padding: 0 2px;
  margin-top: 14px;
}

.sidebar__search :deep(.el-input__wrapper) {
  height: 38px;
  background: #fff;
  border: 1px solid var(--atf-line);
  box-shadow: none;
}

.sidebar__label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 8px 10px;
  color: var(--atf-muted);
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.sidebar__label small {
  display: grid;
  min-width: 19px;
  height: 19px;
  padding: 0 5px;
  color: var(--atf-brand);
  font-size: 10px;
  background: var(--atf-brand-soft);
  border-radius: 999px;
  place-items: center;
}

.sidebar__archive-toggle {
  display: flex;
  align-items: center;
  width: 100%;
  padding: 7px 9px;
  margin-bottom: 6px;
  color: var(--atf-muted);
  font-size: 10.5px;
  font-weight: 600;
  background: transparent;
  border: 0;
  border-radius: 9px;
  cursor: pointer;
  gap: 6px;
}

.sidebar__archive-toggle:hover,
.sidebar__archive-toggle.is-open {
  color: var(--atf-brand-deep);
  background: var(--atf-brand-soft);
}

.sidebar__archive-toggle span {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}

.sidebar__item.is-archived .sidebar__item-title {
  color: var(--atf-muted);
}

.sidebar__list {
  flex: 1;
  padding: 2px 2px 8px;
  overflow-y: auto;
}

.sidebar__item {
  position: relative;
  display: flex;
  align-items: flex-start;
  width: 100%;
  padding: 12px 11px 12px 13px;
  margin-bottom: 4px;
  overflow: hidden;
  text-align: left;
  background: transparent;
  border: 0;
  border-radius: 13px;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.22, 1, 0.36, 1);
  gap: 8px;
}

.sidebar__item:hover {
  background: rgba(255, 255, 255, 0.88);
}

.sidebar__item.is-active {
  background: #fff;
  box-shadow: 0 6px 20px rgba(43, 38, 95, 0.07);
}

.sidebar__active-bar {
  position: absolute;
  top: 50%;
  left: 0;
  width: 3px;
  height: 0;
  background: linear-gradient(180deg, var(--atf-brand), var(--atf-mint));
  border-radius: 0 3px 3px 0;
  transform: translateY(-50%);
  transition: height 0.22s ease;
}

.sidebar__item.is-active .sidebar__active-bar {
  height: 26px;
}

.sidebar__item-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  gap: 4px;
}

.sidebar__item-title {
  overflow: hidden;
  color: var(--atf-ink-soft);
  font-size: 13px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sidebar__item.is-active .sidebar__item-title {
  color: var(--atf-brand-deep);
}

.sidebar__item-preview {
  overflow: hidden;
  color: var(--atf-muted);
  font-size: 11px;
  line-height: 1.5;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sidebar__item-side {
  display: flex;
  flex: none;
  flex-direction: column;
  align-items: flex-end;
  min-width: 30px;
  gap: 5px;
}

.sidebar__item-time {
  color: #b2b5c8;
  font-size: 9.5px;
  white-space: nowrap;
}

.sidebar__item-menu {
  display: grid;
  width: 22px;
  height: 20px;
  color: var(--atf-muted);
  border-radius: 6px;
  opacity: 0;
  transition: opacity 0.18s ease;
  place-items: center;
}

.sidebar__item:hover .sidebar__item-menu,
.sidebar__item.is-active .sidebar__item-menu {
  opacity: 1;
}

.sidebar__item-menu:hover {
  color: var(--atf-brand);
  background: var(--atf-brand-soft);
}

.sidebar__skeleton {
  padding: 10px;
}

.sidebar__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 38px 12px;
  color: var(--atf-muted);
  text-align: center;
  gap: 5px;
}

.sidebar__empty span {
  font-size: 12.5px;
  font-weight: 600;
}

.sidebar__empty small {
  color: #b2b5c8;
  font-size: 10.5px;
}

.sidebar__footer {
  display: flex;
  align-items: flex-start;
  padding: 12px 12px 4px;
  margin-top: 6px;
  border-top: 1px dashed var(--atf-line);
  gap: 9px;
}

.sidebar__footer-icon {
  flex: none;
  width: 22px;
  height: 22px;
  padding: 3px;
  color: var(--atf-brand);
  background: var(--atf-brand-soft);
  border-radius: 7px;
}

.sidebar__footer-icon svg {
  width: 100%;
  height: 100%;
}

.sidebar__footer p {
  color: #a3a6bd;
  font-size: 9.5px;
  line-height: 1.6;
}

@media (max-width: 960px) {
  .sidebar {
    width: 100%;
    min-width: 0;
    border-right: 0;
  }
}
</style>
