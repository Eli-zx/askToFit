<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ChatDotRound,
  DataAnalysis,
  Menu,
  Plus,
  Promotion,
  Setting,
  VideoPause,
} from '@element-plus/icons-vue'

import {
  archiveConversation,
  deleteConversation,
  fetchChatOptions,
  getConversation,
  listConversations,
  listMessages,
  streamMessage,
  unarchiveConversation,
  updateConversation,
} from '@/api/chat'
import { listKnowledgeBases } from '@/api/knowledge'
import ChatMessage from '@/components/ChatMessage.vue'
import ChatSidebar from '@/components/ChatSidebar.vue'
import ConversationSettingsDialog from '@/components/ConversationSettingsDialog.vue'
import KnowledgeGapDrawer from '@/components/KnowledgeGapDrawer.vue'
import { useAuthStore } from '@/stores/auth'
import { SAMPLE_QUESTIONS } from '@/utils/constants'
import { toErrorMessage } from '@/utils/errors'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const options = ref(null)
const knowledgeBases = ref([])
const conversations = ref([])
const activeConversation = ref(null)
const messages = ref([])
const loadingConversations = ref(true)
const loadingMessages = ref(false)
const sending = ref(false)
const loadingOptions = ref(false)

const settingsVisible = ref(false)
const settingsTarget = ref(null)
const pendingQuestion = ref('')
const gapVisible = ref(false)
const mobileSidebar = ref(false)

const input = ref('')
const messagesRef = ref()
const inputRef = ref()
const stickToBottom = ref(true)

let abortController = null
let scrollFrame = 0
let loadToken = 0
// 新建会话后紧接着发送首问，路由变化会触发 watcher；
// 用一次性标记避免重新拉取消息覆盖刚刚写入的用户消息与流式草稿。
let skipLoadForId = null

const conversationId = computed(() => {
  const value = Number(route.params.id)
  return Number.isFinite(value) && value > 0 ? value : null
})

const activeKbs = computed(() => {
  const ids = activeConversation.value?.kb_ids || []
  return knowledgeBases.value.filter((kb) => ids.includes(kb.id))
})

const modelLabel = computed(() => {
  const name = activeConversation.value?.model_name
  if (!name) return ''
  return options.value?.models?.find((item) => item.name === name)?.label || name
})

const hasMessages = computed(() => messages.value.length > 0)
const canSend = computed(
  () => Boolean(input.value.trim()) && Boolean(activeConversation.value) && !sending.value,
)

const kbDocumentTotal = computed(() =>
  activeKbs.value.reduce((total, kb) => total + (kb.document_count || 0), 0),
)

onMounted(async () => {
  await Promise.all([loadOptions(), loadKnowledgeBases()])
  await refreshConversations(true)
  const firstActive = conversations.value.find((item) => !item.archived_at)
  if (!conversationId.value && firstActive) {
    await router.replace(`/chat/${firstActive.id}`)
  }
})

watch(
  conversationId,
  async (id) => {
    if (!id) {
      activeConversation.value = null
      messages.value = []
      return
    }
    if (skipLoadForId === id) {
      skipLoadForId = null
      return
    }
    if (activeConversation.value?.id === id) return
    await loadConversation(id)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  abortController?.abort()
  if (scrollFrame) cancelAnimationFrame(scrollFrame)
})

async function loadOptions() {
  loadingOptions.value = true
  try {
    options.value = await fetchChatOptions()
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '模型配置加载失败'))
  } finally {
    loadingOptions.value = false
  }
}

async function loadKnowledgeBases() {
  try {
    knowledgeBases.value = await listKnowledgeBases()
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '知识库加载失败'))
  }
}

async function refreshConversations(showLoading = false) {
  if (showLoading) loadingConversations.value = true
  try {
    conversations.value = await listConversations(true)
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '会话列表加载失败'))
  } finally {
    loadingConversations.value = false
  }
}

function normalizeMessage(item) {
  return {
    ...item,
    sources: item.sources || [],
    authorInitial: item.role === 'user' ? auth.initials : undefined,
  }
}

async function loadConversation(id) {
  const token = ++loadToken
  loadingMessages.value = true
  try {
    const [conversation, data] = await Promise.all([
      getConversation(id),
      listMessages(id, 100),
    ])
    if (token !== loadToken) return
    activeConversation.value = conversation
    messages.value = (data.items || []).map(normalizeMessage)
    stickToBottom.value = true
    await scrollToBottom(false)
  } catch (error) {
    if (token !== loadToken) return
    ElMessage.error(toErrorMessage(error, '会话加载失败'))
    activeConversation.value = null
    messages.value = []
    await router.replace('/chat')
  } finally {
    if (token === loadToken) loadingMessages.value = false
  }
}

function selectConversation(conversation) {
  mobileSidebar.value = false
  stopGeneration()
  if (conversation.id === activeConversation.value?.id) return
  router.push(`/chat/${conversation.id}`)
}

async function handleRename(conversation, title) {
  try {
    const updated = await updateConversation(conversation.id, { title })
    const index = conversations.value.findIndex((item) => item.id === updated.id)
    if (index >= 0) conversations.value.splice(index, 1, updated)
    if (activeConversation.value?.id === updated.id) {
      activeConversation.value = updated
    }
    ElMessage.success('会话已重命名')
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '重命名失败'))
  }
}

async function handleDelete(conversation) {
  try {
    await deleteConversation(conversation.id)
    ElMessage.success('会话已删除')
    const remaining = conversations.value.filter((item) => item.id !== conversation.id)
    conversations.value = remaining
    if (activeConversation.value?.id === conversation.id) {
      stopGeneration()
      activeConversation.value = null
      messages.value = []
      const next = remaining.find((item) => !item.archived_at)
      if (next) {
        await router.replace(`/chat/${next.id}`)
      } else {
        await router.replace('/chat')
      }
    }
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '删除会话失败'))
  }
}

async function handleArchive(conversation) {
  try {
    const updated = await archiveConversation(conversation.id)
    const index = conversations.value.findIndex((item) => item.id === updated.id)
    if (index >= 0) conversations.value.splice(index, 1, updated)
    ElMessage.success('会话已归档')
    if (activeConversation.value?.id === updated.id) {
      stopGeneration()
      const next = conversations.value.find(
        (item) => !item.archived_at && item.id !== updated.id,
      )
      if (next) {
        await router.replace(`/chat/${next.id}`)
      } else {
        activeConversation.value = null
        messages.value = []
        await router.replace('/chat')
      }
    }
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '归档失败'))
  }
}

async function handleRestore(conversation) {
  try {
    const updated = await unarchiveConversation(conversation.id)
    const index = conversations.value.findIndex((item) => item.id === updated.id)
    if (index >= 0) conversations.value.splice(index, 1, updated)
    ElMessage.success('已取消归档')
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '取消归档失败'))
  }
}

function openCreateDialog(question = '') {
  settingsTarget.value = null
  pendingQuestion.value = String(question || '')
  settingsVisible.value = true
}

function openSettingsDialog() {
  if (!activeConversation.value) {
    openCreateDialog()
    return
  }
  settingsTarget.value = activeConversation.value
  pendingQuestion.value = ''
  settingsVisible.value = true
}

function onSettingsVisibility(visible) {
  settingsVisible.value = visible
  if (!visible) {
    pendingQuestion.value = ''
    settingsTarget.value = null
  }
}

async function handleSettingsSaved({ conversation, isNew }) {
  const question = pendingQuestion.value
  const index = conversations.value.findIndex((item) => item.id === conversation.id)
  if (index >= 0) {
    conversations.value.splice(index, 1, conversation)
  } else {
    conversations.value.unshift(conversation)
  }

  if (isNew) {
    activeConversation.value = conversation
    messages.value = []
    skipLoadForId = conversation.id
    await router.push(`/chat/${conversation.id}`)
    if (question) {
      await sendMessage(question)
    } else {
      await nextTick()
      inputRef.value?.focus()
    }
  } else if (activeConversation.value?.id === conversation.id) {
    activeConversation.value = conversation
  }
}

function useSample(question) {
  if (activeConversation.value) {
    input.value = question
    nextTick(() => inputRef.value?.focus())
  } else {
    openCreateDialog(question)
  }
}

function onScroll() {
  const element = messagesRef.value
  if (!element) return
  stickToBottom.value =
    element.scrollHeight - element.scrollTop - element.clientHeight < 120
}

async function scrollToBottom(smooth = false) {
  await nextTick()
  const element = messagesRef.value
  if (!element) return
  element.scrollTo({
    top: element.scrollHeight,
    behavior: smooth ? 'smooth' : 'auto',
  })
}

function scheduleScroll() {
  if (!stickToBottom.value || scrollFrame) return
  scrollFrame = requestAnimationFrame(() => {
    scrollFrame = 0
    const element = messagesRef.value
    if (element) element.scrollTop = element.scrollHeight
  })
}

function onEnter(event) {
  if (event.isComposing) return
  if (event.shiftKey) return
  event.preventDefault()
  sendMessage()
}

function stopGeneration() {
  abortController?.abort()
}

async function sendMessage(rawText) {
  const text = String(rawText ?? input.value).trim()
  if (!text || sending.value) return

  if (!activeConversation.value) {
    openCreateDialog(text)
    return
  }

  const conversation = activeConversation.value
  messages.value.push({
    id: `local-user-${Date.now()}`,
    role: 'user',
    content: text,
    sources: [],
    created_at: new Date().toISOString(),
    authorInitial: auth.initials,
  })

  const draft = reactive({
    id: `local-assistant-${Date.now()}`,
    role: 'assistant',
    content: '',
    sources: [],
    intent: null,
    intent_confidence: null,
    intent_source: null,
    rewritten_query: null,
    latency_ms: null,
    streaming: true,
    stage: 'rewriting',
    error: '',
  })
  messages.value.push(draft)

  input.value = ''
  sending.value = true
  stickToBottom.value = true
  await scrollToBottom(true)

  abortController = new AbortController()
  try {
    await streamMessage(conversation.id, text, auth.token, abortController.signal, (event, data) => {
      if (event === 'status') {
        draft.stage = data.stage === 'retrieving' ? 'retrieving' : 'rewriting'
        if (data.intent) draft.intent = data.intent
        if (data.intent_confidence != null) draft.intent_confidence = data.intent_confidence
        if (data.intent_source) draft.intent_source = data.intent_source
      } else if (event === 'meta') {
        draft.sources = data.sources || []
        draft.intent = data.intent || draft.intent
        draft.intent_confidence = data.intent_confidence ?? draft.intent_confidence
        draft.intent_source = data.intent_source || draft.intent_source
        draft.rewritten_query = data.rewritten_query || null
        draft.stage = 'generating'
      } else if (event === 'delta') {
        draft.content += data.content || ''
        scheduleScroll()
      } else if (event === 'done') {
        draft.content = data.content ?? draft.content
        draft.sources = data.sources || draft.sources
        draft.intent = data.intent || draft.intent
        draft.intent_confidence = data.intent_confidence ?? draft.intent_confidence
        draft.intent_source = data.intent_source || draft.intent_source
        draft.rewritten_query = data.rewritten_query || draft.rewritten_query
        draft.latency_ms = data.latency_ms ?? null
      } else if (event === 'error') {
        draft.error = data.message || '回答生成失败，请稍后重试'
      }
    })

    if (!draft.content && !draft.error) {
      draft.error = '模型未返回有效内容，请重试'
    }
  } catch (error) {
    if (error.name === 'AbortError') {
      draft.stopped = true
    } else {
      draft.error = toErrorMessage(error, '回答生成失败，请稍后重试')
    }
  } finally {
    draft.streaming = false
    sending.value = false
    abortController = null
    await refreshConversations(false)
    const updated = conversations.value.find((item) => item.id === conversation.id)
    if (updated && activeConversation.value?.id === conversation.id) {
      activeConversation.value = updated
    }
    if (stickToBottom.value) await scrollToBottom(true)
  }
}
</script>

<template>
  <div class="chat">
    <div class="chat__sidebar">
      <ChatSidebar
        :conversations="conversations"
        :active-id="activeConversation?.id"
        :loading="loadingConversations"
        @select="selectConversation"
        @create="openCreateDialog()"
        @rename="handleRename"
        @delete="handleDelete"
        @archive="handleArchive"
        @restore="handleRestore"
      />
    </div>

    <el-drawer
      v-model="mobileSidebar"
      direction="ltr"
      size="300px"
      :with-header="false"
      class="chat__sidebar-drawer"
    >
      <ChatSidebar
        :conversations="conversations"
        :active-id="activeConversation?.id"
        :loading="loadingConversations"
        @select="selectConversation"
        @create="openCreateDialog()"
        @rename="handleRename"
        @delete="handleDelete"
        @archive="handleArchive"
        @restore="handleRestore"
      />
    </el-drawer>

    <section class="chat__main">
      <header class="chat__header">
        <button class="chat__menu" type="button" @click="mobileSidebar = true">
          <el-icon><Menu /></el-icon>
        </button>

        <div class="chat__heading">
          <h1>{{ activeConversation?.title || '智能对话' }}</h1>
          <div v-if="activeConversation" class="chat__heading-meta">
            <span class="chat__model">
              <i />
              {{ modelLabel }}
            </span>
            <span class="chat__divider" />
            <span v-for="kb in activeKbs.slice(0, 3)" :key="kb.id" class="chat__kb">
              {{ kb.name }}
            </span>
            <span v-if="activeKbs.length > 3" class="chat__kb">
              +{{ activeKbs.length - 3 }}
            </span>
            <span class="chat__doc-count">{{ kbDocumentTotal }} 篇文档</span>
          </div>
          <p v-else class="chat__heading-sub">
            在多个知识库之间自由切换，获得有出处的售前答案
          </p>
        </div>

        <div class="chat__header-actions">
          <el-button class="chat__ghost-btn" text @click="gapVisible = true">
            <el-icon><DataAnalysis /></el-icon>
            <span>知识缺口</span>
          </el-button>
          <el-button
            class="chat__ghost-btn"
            text
            :disabled="!activeConversation"
            @click="openSettingsDialog"
          >
            <el-icon><Setting /></el-icon>
            <span>会话设置</span>
          </el-button>
          <el-button class="chat__new-mobile" circle @click="openCreateDialog()">
            <el-icon><Plus /></el-icon>
          </el-button>
        </div>
      </header>

      <div
        ref="messagesRef"
        class="chat__messages atf-scrollbar"
        @scroll="onScroll"
      >
        <div v-if="loadingMessages" class="chat__loading">
          <el-skeleton :rows="6" animated />
        </div>

        <div v-else-if="!activeConversation" class="chat-welcome">
          <div class="chat-welcome__copy">
            <span class="atf-eyebrow">RAG-powered presales</span>
            <h2>
              今天想了解哪件衣服，
              <br />
              或哪条<span class="atf-gradient-text">售后政策</span>？
            </h2>
            <p>
              选择知识库开始对话。系统会先检索资料、重排序，再用大模型组织答案，
              每个结论都可以展开查看引用来源。
            </p>
          </div>

          <div v-if="!knowledgeBases.length" class="chat-welcome__notice">
            <strong>还没有可用的知识库</strong>
            <span>先创建知识库并上传 PDF / PPT / Word / Markdown / 图片文档。</span>
            <RouterLink to="/knowledge">前往创建 →</RouterLink>
          </div>

          <div class="chat-welcome__samples">
            <button
              v-for="sample in SAMPLE_QUESTIONS"
              :key="sample.title"
              type="button"
              class="sample-card"
              :class="`sample-card--${sample.accent}`"
              @click="useSample(sample.text)"
            >
              <span class="sample-card__icon">
                <el-icon><component :is="sample.icon" /></el-icon>
              </span>
              <span class="sample-card__title">{{ sample.title }}</span>
              <span class="sample-card__text">{{ sample.text }}</span>
              <span class="sample-card__arrow">↗</span>
            </button>
          </div>
        </div>

        <div v-else-if="!hasMessages" class="chat-empty">
          <div class="chat-empty__orb">
            <el-icon><ChatDotRound /></el-icon>
          </div>
          <h2>开始一段新的售前咨询</h2>
          <p>
            当前知识库已就绪：{{ activeKbs.map((kb) => kb.name).join('、') || '—' }}
          </p>
          <div class="chat-empty__chips">
            <button
              v-for="sample in SAMPLE_QUESTIONS"
              :key="sample.title"
              type="button"
              @click="useSample(sample.text)"
            >
              {{ sample.title }}
            </button>
          </div>
        </div>

        <div v-else class="chat__thread">
          <ChatMessage
            v-for="(message, index) in messages"
            :key="message.id || index"
            :message="message"
            :question="
              message.role === 'assistant'
                ? messages.slice(0, index).reverse().find((item) => item.role === 'user')?.content || ''
                : ''
            "
            :streaming="Boolean(message.streaming)"
          />
          <div class="chat__thread-end" />
        </div>
      </div>

      <footer class="chat__composer">
        <div class="composer">
          <div class="composer__context" v-if="activeConversation">
            <span class="composer__context-label">检索范围</span>
            <span v-for="kb in activeKbs" :key="kb.id" class="composer__kb">
              {{ kb.name }}
            </span>
          </div>

          <div class="composer__box" :class="{ 'is-focus': input }">
            <textarea
              ref="inputRef"
              v-model="input"
              class="composer__input atf-scrollbar"
              rows="1"
              maxlength="4000"
              placeholder="输入你的售前问题，例如：满 199 元包邮吗？"
              @keydown.enter="onEnter"
            />
            <div class="composer__actions">
              <span class="composer__hint">
                <template v-if="input.length">{{ input.length }} / 4000</template>
                <template v-else>Enter 发送 · Shift + Enter 换行</template>
              </span>
              <el-button
                v-if="sending"
                class="composer__stop"
                circle
                @click="stopGeneration"
              >
                <el-icon><VideoPause /></el-icon>
              </el-button>
              <el-button
                v-else
                class="composer__send"
                type="primary"
                circle
                :disabled="!canSend"
                @click="sendMessage()"
              >
                <el-icon><Promotion /></el-icon>
              </el-button>
            </div>
          </div>

          <p class="composer__legal">
            回答由知识库检索增强生成，请以引用资料与结算页为准。
          </p>
        </div>
      </footer>
    </section>

    <ConversationSettingsDialog
      :model-value="settingsVisible"
      :conversation="settingsTarget"
      :knowledge-bases="knowledgeBases"
      :options="options"
      :pending-question="pendingQuestion"
      @update:model-value="onSettingsVisibility"
      @saved="handleSettingsSaved"
    />

    <KnowledgeGapDrawer v-model="gapVisible" />
  </div>
</template>

<style scoped>
.chat {
  display: flex;
  height: calc(100vh - var(--atf-header-height));
  margin-top: var(--atf-header-height);
  overflow: hidden;
  background:
    radial-gradient(circle at 78% 8%, rgba(139, 124, 246, 0.08), transparent 34%),
    radial-gradient(circle at 20% 92%, rgba(45, 212, 191, 0.07), transparent 30%),
    #fbfaff;
}

.chat__sidebar {
  flex: none;
  height: 100%;
}

.chat__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  height: 100%;
}

.chat__header {
  display: flex;
  flex: none;
  align-items: center;
  height: 76px;
  padding: 0 28px;
  background: rgba(255, 255, 255, 0.66);
  border-bottom: 1px solid var(--atf-line);
  backdrop-filter: blur(14px);
  gap: 14px;
}

.chat__menu {
  display: none;
  width: 36px;
  height: 36px;
  color: var(--atf-ink-soft);
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 10px;
  cursor: pointer;
}

.chat__heading {
  min-width: 0;
}

.chat__heading h1 {
  overflow: hidden;
  color: var(--atf-ink);
  font-size: 16px;
  font-weight: 750;
  letter-spacing: -0.015em;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat__heading-meta {
  display: flex;
  align-items: center;
  margin-top: 5px;
  overflow: hidden;
  gap: 7px;
}

.chat__heading-sub {
  margin-top: 4px;
  color: var(--atf-muted);
  font-size: 11px;
}

.chat__model {
  display: inline-flex;
  flex: none;
  align-items: center;
  color: var(--atf-brand-deep);
  font-size: 10px;
  font-weight: 650;
  gap: 5px;
}

.chat__model i {
  width: 5px;
  height: 5px;
  background: var(--atf-mint);
  border-radius: 50%;
  box-shadow: 0 0 0 3px rgba(45, 212, 191, 0.14);
}

.chat__divider {
  flex: none;
  width: 1px;
  height: 10px;
  background: var(--atf-line-strong);
}

.chat__kb {
  flex: none;
  padding: 2px 7px;
  color: #5f6280;
  font-size: 9.5px;
  background: #f2f2f8;
  border-radius: 999px;
  white-space: nowrap;
}

.chat__doc-count {
  flex: none;
  color: #aeb1c5;
  font-size: 9.5px;
}

.chat__header-actions {
  display: flex;
  align-items: center;
  margin-left: auto;
  gap: 4px;
}

.chat__ghost-btn {
  padding: 7px 11px;
  color: var(--atf-muted);
  font-size: 11px;
  border-radius: 9px;
}

.chat__ghost-btn:hover {
  color: var(--atf-brand-deep);
  background: var(--atf-brand-soft);
}

.chat__ghost-btn .el-icon {
  margin-right: 4px;
}

.chat__new-mobile {
  display: none;
}

.chat__messages {
  flex: 1;
  padding: 26px 28px 12px;
  overflow-y: auto;
}

.chat__loading {
  width: min(820px, 100%);
  padding-top: 20px;
  margin: 0 auto;
}

.chat__thread {
  display: flex;
  flex-direction: column;
  width: min(900px, 100%);
  margin: 0 auto;
  gap: 24px;
}

.chat__thread-end {
  height: 4px;
}

/* ---------- 欢迎页 ---------- */
.chat-welcome {
  display: flex;
  flex-direction: column;
  width: min(900px, 100%);
  padding: 5vh 0 30px;
  margin: 0 auto;
  animation: atf-rise 0.55s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.chat-welcome__copy h2 {
  max-width: 620px;
  margin-top: 20px;
  font-size: clamp(30px, 4vw, 47px);
  font-weight: 800;
  letter-spacing: -0.04em;
  line-height: 1.22;
}

.chat-welcome__copy p {
  max-width: 560px;
  margin-top: 16px;
  color: var(--atf-muted);
  font-size: 13px;
  line-height: 1.85;
}

.chat-welcome__notice {
  display: flex;
  flex-direction: column;
  padding: 14px 17px;
  margin-top: 24px;
  background: #fffaf0;
  border: 1px solid rgba(202, 138, 4, 0.16);
  border-radius: 13px;
  gap: 3px;
}

.chat-welcome__notice strong {
  color: #8a5c14;
  font-size: 12.5px;
}

.chat-welcome__notice span {
  color: #a07b3e;
  font-size: 11px;
}

.chat-welcome__notice a {
  margin-top: 4px;
  font-size: 11px;
  font-weight: 700;
}

.chat-welcome__samples {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-top: 38px;
  gap: 12px;
}

.sample-card {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  padding: 18px 20px 17px;
  overflow: hidden;
  text-align: left;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 17px;
  cursor: pointer;
  transition: all 0.28s cubic-bezier(0.22, 1, 0.36, 1);
}

.sample-card::before {
  position: absolute;
  top: -50px;
  right: -50px;
  width: 130px;
  height: 130px;
  content: '';
  background: var(--sample-glow, rgba(139, 124, 246, 0.12));
  border-radius: 50%;
  opacity: 0.7;
  transition: transform 0.4s ease;
}

.sample-card:hover {
  border-color: rgba(91, 91, 214, 0.2);
  box-shadow: var(--atf-shadow-md);
  transform: translateY(-3px);
}

.sample-card:hover::before {
  transform: scale(1.5);
}

.sample-card--mint {
  --sample-glow: rgba(45, 212, 191, 0.15);
}

.sample-card--violet {
  --sample-glow: rgba(139, 124, 246, 0.16);
}

.sample-card--peach {
  --sample-glow: rgba(255, 138, 112, 0.15);
}

.sample-card--blue {
  --sample-glow: rgba(56, 132, 255, 0.13);
}

.sample-card__icon {
  display: grid;
  width: 34px;
  height: 34px;
  color: var(--atf-brand);
  font-size: 16px;
  background: var(--atf-brand-soft);
  border-radius: 10px;
  place-items: center;
}

.sample-card__title {
  margin-top: 12px;
  color: var(--atf-ink);
  font-size: 13.5px;
  font-weight: 700;
}

.sample-card__text {
  margin-top: 5px;
  color: var(--atf-muted);
  font-size: 11px;
  line-height: 1.65;
}

.sample-card__arrow {
  position: absolute;
  top: 18px;
  right: 18px;
  color: #b8bad0;
  font-size: 14px;
  transition: all 0.2s ease;
}

.sample-card:hover .sample-card__arrow {
  color: var(--atf-brand);
  transform: translate(2px, -2px);
}

/* ---------- 空会话 ---------- */
.chat-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  padding: 40px 20px 80px;
  text-align: center;
  animation: atf-rise 0.5s ease both;
}

.chat-empty__orb {
  display: grid;
  width: 66px;
  height: 66px;
  color: #fff;
  font-size: 27px;
  background: linear-gradient(135deg, #5b5bd6, #8b7cf6 60%, #2dd4bf 140%);
  border-radius: 22px;
  box-shadow: 0 16px 36px rgba(91, 91, 214, 0.24);
  place-items: center;
}

.chat-empty h2 {
  margin-top: 20px;
  font-size: 21px;
  font-weight: 750;
  letter-spacing: -0.02em;
}

.chat-empty p {
  margin-top: 8px;
  color: var(--atf-muted);
  font-size: 12px;
}

.chat-empty__chips {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  margin-top: 22px;
  gap: 8px;
}

.chat-empty__chips button {
  padding: 8px 13px;
  color: var(--atf-ink-soft);
  font-size: 11.5px;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.chat-empty__chips button:hover {
  color: var(--atf-brand-deep);
  border-color: rgba(91, 91, 214, 0.28);
  box-shadow: var(--atf-shadow-sm);
}

/* ---------- 输入区 ---------- */
.chat__composer {
  flex: none;
  padding: 8px 28px 18px;
  background: linear-gradient(180deg, rgba(251, 250, 255, 0), rgba(251, 250, 255, 0.96) 22%);
}

.composer {
  width: min(900px, 100%);
  margin: 0 auto;
}

.composer__context {
  display: flex;
  align-items: center;
  margin-bottom: 7px;
  overflow: hidden;
  gap: 6px;
}

.composer__context-label {
  flex: none;
  color: #a3a6bd;
  font-size: 9.5px;
}

.composer__kb {
  flex: none;
  padding: 2px 8px;
  color: #6c6f8c;
  font-size: 9.5px;
  background: rgba(91, 91, 214, 0.07);
  border-radius: 999px;
}

.composer__box {
  padding: 12px 12px 9px 16px;
  background: #fff;
  border: 1px solid var(--atf-line-strong);
  border-radius: 18px;
  box-shadow: 0 10px 34px rgba(43, 38, 95, 0.08);
  transition: all 0.22s ease;
}

.composer__box:focus-within {
  border-color: rgba(91, 91, 214, 0.4);
  box-shadow:
    0 0 0 4px rgba(91, 91, 214, 0.07),
    0 12px 34px rgba(43, 38, 95, 0.1);
}

.composer__input {
  display: block;
  width: 100%;
  min-height: 30px;
  max-height: 150px;
  padding: 0;
  overflow-y: auto;
  color: var(--atf-ink-soft);
  font-size: 13.5px;
  line-height: 1.7;
  resize: none;
  background: transparent;
  border: 0;
  outline: none;
}

.composer__input::placeholder {
  color: #b3b6cb;
}

.composer__actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 7px;
}

.composer__hint {
  color: #b0b3c7;
  font-size: 9.5px;
  font-variant-numeric: tabular-nums;
}

.composer__send,
.composer__stop {
  width: 34px;
  height: 34px;
  border: 0;
}

.composer__send {
  box-shadow: 0 6px 16px rgba(91, 91, 214, 0.28);
}

.composer__stop {
  color: var(--atf-brand);
  background: var(--atf-brand-soft);
}

.composer__legal {
  margin-top: 7px;
  color: #b3b6c9;
  font-size: 9px;
  text-align: center;
}

:deep(.chat__sidebar-drawer .el-drawer__body) {
  padding: 0;
}

@media (max-width: 960px) {
  .chat__sidebar {
    display: none;
  }

  .chat__menu {
    display: grid;
    place-items: center;
  }

  .chat__new-mobile {
    display: inline-flex;
  }

  .chat__header {
    padding: 0 16px;
  }

  .chat__ghost-btn span {
    display: none;
  }

  .chat__messages {
    padding: 18px 16px 8px;
  }

  .chat__composer {
    padding: 6px 14px 12px;
  }

  .chat-welcome {
    padding-top: 2vh;
  }

  .chat-welcome__samples {
    grid-template-columns: 1fr;
    margin-top: 26px;
  }

  .chat__thread {
    gap: 20px;
  }
}
</style>
