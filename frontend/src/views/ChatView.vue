<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ArrowDown,
  ChatDotRound,
  Collection,
  DataAnalysis,
  Menu,
  Plus,
  Setting,
  Sunny,
  VideoPause,
} from '@element-plus/icons-vue'

import {
  archiveConversation,
  createConversation,
  deleteConversation,
  fetchChatOptions,
  getConversation,
  listConversations,
  listMessages,
  streamMessage,
  unarchiveConversation,
  updateConversation,
} from '@/api/chat'
import { redirectToLoginOnce } from '@/api/http'
import { listKnowledgeBases } from '@/api/knowledge'
import ChatMessage from '@/components/ChatMessage.vue'
import ChatSidebar from '@/components/ChatSidebar.vue'
import ConversationSettingsDialog from '@/components/ConversationSettingsDialog.vue'
import KnowledgeGapDrawer from '@/components/KnowledgeGapDrawer.vue'
import { useAuthStore } from '@/stores/auth'
import {
  MAX_KB_SELECTION,
  buildConversationPayload,
  clampTemperature,
  normalizeKbIds,
  pickDefaultKnowledgeBase,
} from '@/utils/chat'
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
const switchingConversation = ref(false)
const loadError = ref('')
// 只有首次进入对话区才展示骨架屏；之后切换会话保留当前内容，只显示顶部进度线
const hasLoadedMessages = ref(false)
const sending = ref(false)
const creatingConversation = ref(false)
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

// 输入框上方工具条的草稿配置：没有会话时用于自动建会话，
// 有会话时同步到会话并持久化，发送前会把未落盘的修改一起提交。
const draftKbIds = ref([])
const draftTemperature = ref(0.3)
const kbPickerVisible = ref(false)
const temperaturePickerVisible = ref(false)
const configSaving = ref(false)
const TEMPERATURE_PRESETS = [
  { label: '严谨', value: 0.1 },
  { label: '平衡', value: 0.3 },
  { label: '发散', value: 0.7 },
]

let abortController = null
let scrollFrame = 0
let loadToken = 0
let temperatureTimer = null
let configSavePromise = Promise.resolve()
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

const composerKbs = computed(() =>
  knowledgeBases.value.filter((kb) => draftKbIds.value.includes(kb.id)),
)

const composerKbLabel = computed(() => {
  if (!composerKbs.value.length) return '选择知识库'
  if (composerKbs.value.length === 1) return composerKbs.value[0].name
  return `${composerKbs.value.length} 个知识库`
})

const composerTemperature = computed(() => draftTemperature.value.toFixed(1))
const canEditComposer = computed(() => !sending.value && !creatingConversation.value)

const modelLabel = computed(() => {
  const name = activeConversation.value?.model_name
  if (!name) return ''
  const option = options.value?.models?.find((item) => item.name === name)
  // 演示界面不暴露服务商型号：默认模型统一显示为“默认模型”，切换过才显示别名
  if (!option) return name
  return option.default ? '默认模型' : option.label
})

const hasMessages = computed(() => messages.value.length > 0)
const canSend = computed(
  () => Boolean(input.value.trim()) && !sending.value && !creatingConversation.value,
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
      loadError.value = ''
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

// 打开已有会话时，输入框上方的控件跟随该会话的配置。
watch(
  activeConversation,
  (conversation) => {
    if (!conversation) return
    draftKbIds.value = normalizeKbIds(conversation.kb_ids)
    draftTemperature.value = clampTemperature(conversation.temperature)
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
    if (!activeConversation.value && options.value?.defaults?.temperature != null) {
      draftTemperature.value = clampTemperature(options.value.defaults.temperature)
    }
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '模型配置加载失败'))
  } finally {
    loadingOptions.value = false
  }
}

async function loadKnowledgeBases() {
  try {
    knowledgeBases.value = await listKnowledgeBases()
    if (!activeConversation.value && !draftKbIds.value.length) {
      const defaultKb = pickDefaultKnowledgeBase(knowledgeBases.value)
      if (defaultKb) draftKbIds.value = [Number(defaultKb.id)]
    }
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
  const firstLoad = !hasLoadedMessages.value
  loadError.value = ''
  if (firstLoad) loadingMessages.value = true
  else switchingConversation.value = true
  try {
    const [conversation, data] = await Promise.all([
      getConversation(id),
      listMessages(id, 100),
    ])
    if (token !== loadToken) return
    activeConversation.value = conversation
    messages.value = (data.items || []).map(normalizeMessage)
    hasLoadedMessages.value = true
    // 先撤掉骨架屏，再滚动；否则滚的是骨架的高度，直接打开会话会停在顶部
    loadingMessages.value = false
    stickToBottom.value = true
    await scrollToBottom(false)
  } catch (error) {
    if (token !== loadToken) return
    ElMessage.error(toErrorMessage(error, '会话加载失败'))
    if (error.response?.status === 404) {
      // 会话确实不存在时才回到欢迎页
      activeConversation.value = null
      messages.value = []
      await router.replace('/chat')
    } else {
      // 网络抖动时保留当前对话，只提示错误，避免整块内容闪空
      loadError.value = '会话加载失败，当前仍显示上一段对话。'
    }
  } finally {
    if (token === loadToken) {
      loadingMessages.value = false
      switchingConversation.value = false
    }
  }
}

function retryLoad() {
  const id = conversationId.value
  if (id) loadConversation(id)
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

function replaceConversation(conversation) {
  const index = conversations.value.findIndex((item) => item.id === conversation.id)
  if (index >= 0) conversations.value.splice(index, 1, conversation)
  else conversations.value.unshift(conversation)
  if (activeConversation.value?.id === conversation.id) {
    activeConversation.value = conversation
  }
}

function sameKbIds(left, right) {
  const a = normalizeKbIds(left)
  const b = normalizeKbIds(right)
  return a.length === b.length && a.every((id) => b.includes(id))
}

async function persistComposerConfig(patch) {
  const conversation = activeConversation.value
  if (!conversation || sending.value) return
  const conversationId = conversation.id
  configSaving.value = true
  configSavePromise = configSavePromise.then(async () => {
    try {
      const updated = await updateConversation(conversationId, patch)
      replaceConversation(updated)
    } catch (error) {
      ElMessage.error(toErrorMessage(error, '会话配置保存失败'))
      // 保存失败时以服务端为准，避免界面停留在未生效的配置上
      try {
        const fresh = await getConversation(conversationId)
        replaceConversation(fresh)
      } catch {
        // 网络不可用时保留当前草稿，下一次操作会再次尝试
      }
    } finally {
      configSaving.value = false
    }
  })
  return configSavePromise
}

function toggleKb(kbId, checked) {
  if (!canEditComposer.value) return
  const id = Number(kbId)
  const next = normalizeKbIds(
    checked
      ? [...draftKbIds.value, id]
      : draftKbIds.value.filter((item) => item !== id),
  )
  if (!next.length) {
    ElMessage.warning('至少保留一个知识库')
    return
  }
  if (next.length > MAX_KB_SELECTION) {
    ElMessage.warning(`最多选择 ${MAX_KB_SELECTION} 个知识库`)
    return
  }
  draftKbIds.value = next
  if (!activeConversation.value) return
  if (sameKbIds(next, activeConversation.value.kb_ids)) return
  persistComposerConfig({ kb_ids: next })
}

function onTemperatureInput(value) {
  if (!canEditComposer.value) return
  draftTemperature.value = clampTemperature(value)
  if (!activeConversation.value) return
  if (temperatureTimer) clearTimeout(temperatureTimer)
  temperatureTimer = setTimeout(() => {
    temperatureTimer = null
    persistComposerConfig({ temperature: draftTemperature.value })
  }, 300)
}

function applyTemperaturePreset(value) {
  onTemperatureInput(value)
  flushTemperature()
}

async function flushTemperature() {
  if (temperatureTimer) {
    clearTimeout(temperatureTimer)
    temperatureTimer = null
  }
  if (!activeConversation.value) return
  const current = clampTemperature(activeConversation.value.temperature)
  if (Math.abs(current - draftTemperature.value) < 0.001) return
  await persistComposerConfig({ temperature: draftTemperature.value })
}

async function flushComposerConfig() {
  await flushTemperature()
  await configSavePromise
}

function startNewConversation() {
  stopGeneration()
  if (temperatureTimer) {
    clearTimeout(temperatureTimer)
    temperatureTimer = null
  }
  mobileSidebar.value = false
  activeConversation.value = null
  messages.value = []
  loadError.value = ''
  input.value = ''
  if (route.path !== '/chat') router.push('/chat')
  nextTick(() => inputRef.value?.focus())
}

function useSample(question) {
  input.value = question
  nextTick(() => inputRef.value?.focus())
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

async function ensureDefaultConversation() {
  if (activeConversation.value) return activeConversation.value

  const payload = buildConversationPayload(knowledgeBases.value, {
    kbIds: draftKbIds.value,
    temperature: draftTemperature.value,
  })
  if (!payload) {
    ElMessage.warning(
      auth.user?.is_admin
        ? '暂无可用知识库，请先创建并上传文档'
        : '暂无可用知识库，请联系管理员',
    )
    return null
  }

  creatingConversation.value = true
  try {
    const conversation = await createConversation(payload)
    conversations.value.unshift(conversation)
    activeConversation.value = conversation
    messages.value = []
    skipLoadForId = conversation.id
    await router.push(`/chat/${conversation.id}`)
    return conversation
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '创建对话失败'))
    return null
  } finally {
    creatingConversation.value = false
  }
}

async function sendMessage(rawText) {
  const text = String(rawText ?? input.value).trim()
  if (!text || sending.value || creatingConversation.value) return

  // 切会话失败时页面仍显示上一段对话，发送前先对齐路由指向的会话
  if (
    conversationId.value &&
    activeConversation.value?.id !== conversationId.value
  ) {
    await loadConversation(conversationId.value)
    if (activeConversation.value?.id !== conversationId.value) return
  }

  if (!activeConversation.value) {
    const created = await ensureDefaultConversation()
    if (!created) return
  }

  // 拖动温度或切换知识库后立刻发送时，先确保配置已落盘再发起问答
  await flushComposerConfig()
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
    } else if (error.status === 401) {
      redirectToLoginOnce()
      draft.error = '登录已过期，请重新登录'
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
        @create="startNewConversation"
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
        @create="startNewConversation"
        @rename="handleRename"
        @delete="handleDelete"
        @archive="handleArchive"
        @restore="handleRestore"
      />
    </el-drawer>

    <section class="chat__main">
      <div
        v-if="switchingConversation"
        class="chat__progress"
        aria-hidden="true"
      />
      <header class="chat__header">
        <button class="chat__menu" type="button" @click="mobileSidebar = true">
          <el-icon><Menu /></el-icon>
        </button>

        <div class="chat__heading">
          <h1>{{ activeConversation?.title || '智能对话' }}</h1>
          <div v-if="activeConversation" class="chat__heading-meta">
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
          <el-button
            v-if="auth.user?.is_admin"
            class="chat__ghost-btn"
            text
            @click="gapVisible = true"
          >
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
        <div v-if="loadError" class="chat__load-error" role="alert">
          <span>{{ loadError }}</span>
          <button type="button" @click="retryLoad">重试</button>
        </div>

        <div v-if="loadingMessages" class="chat__loading">
          <el-skeleton :rows="6" animated />
        </div>

        <div v-else-if="!activeConversation" class="chat-welcome">
          <div class="chat-welcome__copy">
            <span class="atf-eyebrow">Private client advisory</span>
            <h2>
              今天想了解哪件衣服，
              <br />
              或哪条<span class="atf-gradient-text">售后政策</span>？
            </h2>
            <p>
              选好知识库，直接描述场景或问题。回答会附上依据，
              每个结论都能展开核对出处。
            </p>
          </div>

          <div v-if="!knowledgeBases.length" class="chat-welcome__notice">
            <strong>还没有可用的知识库</strong>
            <span v-if="auth.user?.is_admin">
              先创建知识库并上传 PDF / PPT / Word / Markdown / 图片文档。
            </span>
            <span v-else>请联系管理员添加公共知识库后再试。</span>
            <RouterLink v-if="auth.user?.is_admin" to="/knowledge">
              前往创建 →
            </RouterLink>
          </div>

          <div class="chat-welcome__samples">
            <span class="chat-welcome__samples-label">Common questions</span>
            <button
              v-for="(sample, index) in SAMPLE_QUESTIONS"
              :key="sample.title"
              type="button"
              class="sample-row"
              @click="useSample(sample.text)"
            >
              <span class="sample-row__index">
                {{ String(index + 1).padStart(2, '0') }}
              </span>
              <span class="sample-row__body">
                <strong>{{ sample.title }}</strong>
                <small>{{ sample.text }}</small>
              </span>
              <span class="sample-row__arrow">↗</span>
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
          <div class="composer__toolbar">
            <el-popover
              v-model:visible="kbPickerVisible"
              placement="top-start"
              :width="340"
              trigger="click"
              popper-class="composer-popover"
              :disabled="!knowledgeBases.length || !canEditComposer"
            >
              <template #reference>
                <button
                  class="composer__tool"
                  type="button"
                  :disabled="!knowledgeBases.length || !canEditComposer"
                >
                  <el-icon><Collection /></el-icon>
                  <span class="composer__tool-label">知识库</span>
                  <b class="composer__tool-value">{{ composerKbLabel }}</b>
                  <el-icon class="composer__tool-caret"><ArrowDown /></el-icon>
                </button>
              </template>

              <div class="kb-picker">
                <div class="kb-picker__head">
                  <span>检索范围</span>
                  <small>至少 1 个 · 最多 {{ MAX_KB_SELECTION }} 个</small>
                </div>
                <div class="kb-picker__list atf-scrollbar">
                  <el-checkbox
                    v-for="kb in knowledgeBases"
                    :key="kb.id"
                    class="kb-picker__item"
                    :class="{ 'is-checked': draftKbIds.includes(kb.id) }"
                    :model-value="draftKbIds.includes(kb.id)"
                    :disabled="
                      !canEditComposer ||
                      (draftKbIds.length === 1 && draftKbIds.includes(kb.id))
                    "
                    @change="toggleKb(kb.id, $event)"
                  >
                    <span class="kb-picker__body">
                      <span class="kb-picker__name">
                        {{ kb.name }}
                        <em v-if="kb.is_public">公共</em>
                      </span>
                      <span class="kb-picker__meta">
                        {{ kb.document_count || 0 }} 篇文档
                      </span>
                    </span>
                  </el-checkbox>
                  <p v-if="!knowledgeBases.length" class="kb-picker__empty">
                    {{ auth.user?.is_admin ? '暂无知识库，请先在「知识库」中创建。' : '暂无可用知识库，请联系管理员。' }}
                  </p>
                </div>
                <RouterLink
                  v-if="auth.user?.is_admin"
                  class="kb-picker__manage"
                  to="/knowledge"
                >
                  管理知识库 →
                </RouterLink>
              </div>
            </el-popover>

            <el-popover
              v-model:visible="temperaturePickerVisible"
              placement="top-end"
              :width="300"
              trigger="click"
              popper-class="composer-popover"
            >
              <template #reference>
                <button
                  class="composer__tool"
                  type="button"
                  :disabled="!canEditComposer"
                >
                  <el-icon><Sunny /></el-icon>
                  <span class="composer__tool-label">温度</span>
                  <b class="composer__tool-value">{{ composerTemperature }}</b>
                </button>
              </template>

              <div class="temp-picker">
                <div class="temp-picker__head">
                  <span>回答温度</span>
                  <b>{{ composerTemperature }}</b>
                </div>
                <el-slider
                  :model-value="draftTemperature"
                  :min="0"
                  :max="2"
                  :step="0.1"
                  :disabled="!canEditComposer"
                  :show-tooltip="false"
                  @input="onTemperatureInput"
                  @change="flushTemperature"
                />
                <div class="temp-picker__presets">
                  <button
                    v-for="preset in TEMPERATURE_PRESETS"
                    :key="preset.value"
                    type="button"
                    :class="{
                      'is-active': Math.abs(draftTemperature - preset.value) < 0.05,
                    }"
                    :disabled="!canEditComposer"
                    @click="applyTemperaturePreset(preset.value)"
                  >
                    <b>{{ preset.label }}</b>
                    <span>{{ preset.value.toFixed(1) }}</span>
                  </button>
                </div>
                <p>越低越严谨，政策与价格问题建议 0.1–0.4；修改从下一轮开始生效。</p>
              </div>
            </el-popover>

            <span v-if="configSaving" class="composer__saving">保存中…</span>
            <span class="composer__toolbar-meta">
              <i />
              {{ modelLabel || '默认模型' }}
            </span>
          </div>

          <div class="composer__box" :class="{ 'is-focus': input }">
            <textarea
              ref="inputRef"
              v-model="input"
              class="composer__input atf-scrollbar"
              rows="1"
              maxlength="4000"
              placeholder="描述场景和需求，例如通勤选什么外套"
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
                :disabled="!canSend"
                @click="sendMessage()"
              >
                发送
                <span class="composer__send-arrow" aria-hidden="true">↗</span>
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

    <KnowledgeGapDrawer v-if="auth.user?.is_admin" v-model="gapVisible" />
  </div>
</template>

<style scoped>
.chat {
  display: flex;
  height: calc(100vh - var(--atf-header-height));
  margin-top: var(--atf-header-height);
  overflow: hidden;
  background: var(--atf-bg);
}

.chat__sidebar {
  flex: none;
  height: 100%;
}

.chat__main {
  position: relative;
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  height: 100%;
}

/* 切换会话时的 1px 顶部进度线，避免整块骨架屏闪烁 */
.chat__progress {
  position: absolute;
  z-index: 5;
  top: 0;
  left: 0;
  width: 34%;
  height: 1px;
  background: var(--atf-brand);
  animation: chat-progress 1.1s ease-in-out infinite;
}

@keyframes chat-progress {
  0% {
    transform: translateX(-100%);
  }

  100% {
    transform: translateX(390%);
  }
}

.chat__load-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: min(820px, 100%);
  padding: 8px 10px;
  margin: 0 auto 12px;
  color: var(--atf-brand-deep);
  font-size: 11px;
  border: 1px solid var(--atf-line-strong);
  gap: 12px;
}

.chat__load-error button {
  flex: none;
  padding: 2px 0;
  color: var(--atf-brand);
  font-size: 10.5px;
  background: transparent;
  border: 0;
  border-bottom: 1px solid currentColor;
  cursor: pointer;
}

.chat__header {
  display: flex;
  flex: none;
  align-items: center;
  height: 78px;
  padding: 0 30px;
  background: var(--atf-bg);
  border-bottom: 1px solid var(--atf-line-strong);
  gap: 14px;
}

.chat__menu {
  display: none;
  width: 34px;
  height: 34px;
  color: var(--atf-ink-soft);
  background: var(--atf-surface-raised);
  border: 1px solid var(--atf-line-strong);
  border-radius: var(--atf-radius-sm);
  cursor: pointer;
}

.chat__heading {
  min-width: 0;
}

.chat__heading h1 {
  overflow: hidden;
  color: var(--atf-ink);
  font-family: var(--atf-font-display);
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.01em;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat__heading-meta {
  display: flex;
  align-items: center;
  margin-top: 6px;
  overflow: hidden;
  gap: 8px;
}

.chat__heading-sub {
  margin-top: 4px;
  color: var(--atf-muted);
  font-size: 11px;
}

.chat__kb {
  flex: none;
  overflow: hidden;
  color: var(--atf-muted);
  font-size: 9.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat__kb + .chat__kb::before {
  margin-right: 8px;
  content: '·';
  color: var(--atf-line-strong);
}

.chat__doc-count {
  flex: none;
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 9px;
}

.chat__header-actions {
  display: flex;
  align-items: center;
  margin-left: auto;
  gap: 10px;
}

.chat__ghost-btn {
  padding: 4px 0;
  color: var(--atf-muted);
  font-size: 10.5px;
  letter-spacing: 0.02em;
  border-radius: 0;
}

.chat__ghost-btn:hover {
  color: var(--atf-brand);
  background: transparent;
  text-decoration: underline;
  text-underline-offset: 4px;
}

.chat__ghost-btn .el-icon {
  margin-right: 4px;
}

.chat__new-mobile {
  display: none;
}

.chat__messages {
  flex: 1;
  padding: 30px 28px 8px;
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
  width: min(820px, 100%);
  margin: 0 auto;
  gap: 26px;
}

/* 给最后一条引用留出呼吸空间，展开后不会被输入区压住 */
.chat__thread-end {
  height: 64px;
}

/* 阅读栏收窄、正文放大，长回答不铺满整屏 */
.chat__thread .markdown-body {
  font-size: 15.5px;
  line-height: 1.78;
}

/* ---------- 欢迎页 ---------- */
.chat-welcome {
  display: flex;
  flex-direction: column;
  width: min(900px, 100%);
  padding: 3vh 0 24px;
  margin: 0 auto;
  animation: atf-rise 0.55s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.chat-welcome__copy h2 {
  max-width: 640px;
  margin-top: 20px;
  font-family: var(--atf-font-display);
  font-size: clamp(31px, 4vw, 48px);
  font-weight: 600;
  letter-spacing: -0.025em;
  line-height: 1.24;
}

.chat-welcome__copy p {
  max-width: 540px;
  margin-top: 16px;
  color: var(--atf-muted);
  font-size: 13px;
  line-height: 1.9;
}

.chat-welcome__notice {
  display: flex;
  flex-direction: column;
  padding: 13px 16px;
  margin-top: 24px;
  background: var(--atf-tone-peach-bg);
  border-left: 2px solid var(--atf-peach);
  border-radius: 0;
  gap: 3px;
}

.chat-welcome__notice strong {
  color: var(--atf-tone-gold);
  font-size: 12.5px;
}

.chat-welcome__notice span {
  color: var(--atf-bronze);
  font-size: 11px;
}

.chat-welcome__notice a {
  margin-top: 4px;
  font-size: 11px;
  font-weight: 700;
}

.chat-welcome__samples {
  margin-top: 30px;
  border-top: 1px solid var(--atf-line-strong);
}

.chat-welcome__samples-label {
  display: block;
  padding: 10px 2px 8px;
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 8.5px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  border-bottom: 1px solid var(--atf-line);
}

.sample-row {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) 24px;
  align-items: baseline;
  width: 100%;
  padding: 13px 4px;
  text-align: left;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--atf-line);
  border-radius: 0;
  cursor: pointer;
  gap: 14px;
  transition: background 0.18s ease;
}

.sample-row:hover {
  background: var(--atf-wash);
}

.sample-row__index {
  color: var(--atf-brand);
  font-family: var(--atf-font-mono);
  font-size: 9px;
  letter-spacing: 0.08em;
}

.sample-row__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 4px;
}

.sample-row__body strong {
  color: var(--atf-ink);
  font-family: var(--atf-font-display);
  font-size: 15px;
  font-weight: 600;
}

.sample-row__body small {
  overflow: hidden;
  color: var(--atf-muted);
  font-size: 11px;
  line-height: 1.65;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sample-row__arrow {
  color: var(--atf-muted);
  font-size: 13px;
  text-align: right;
  transition:
    color 0.18s ease,
    transform 0.18s ease;
}

.sample-row:hover .sample-row__arrow {
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
  width: 60px;
  height: 60px;
  color: var(--atf-bg);
  font-size: 24px;
  background: var(--atf-ink);
  border-radius: 2px;
  place-items: center;
}

.chat-empty h2 {
  margin-top: 20px;
  font-family: var(--atf-font-display);
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.015em;
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
  margin-top: 24px;
  gap: 8px;
}

.chat-empty__chips button {
  padding: 7px 12px;
  color: var(--atf-ink-soft);
  font-size: 11px;
  background: transparent;
  border: 1px solid var(--atf-line-strong);
  border-radius: 1px;
  cursor: pointer;
  transition:
    color 0.16s ease,
    border-color 0.16s ease;
}

.chat-empty__chips button:hover {
  color: var(--atf-brand);
  border-color: var(--atf-brand);
}

/* ---------- 输入区 ---------- */
.chat__composer {
  flex: none;
  padding: 10px 30px 18px;
  background: var(--atf-bg);
  border-top: 1px solid var(--atf-line);
}

.composer {
  width: min(900px, 100%);
  margin: 0 auto;
}

.composer__toolbar {
  display: flex;
  align-items: center;
  min-height: 30px;
  margin-bottom: 7px;
  gap: 4px;
}

.composer__tool {
  display: inline-flex;
  align-items: center;
  max-width: 360px;
  padding: 5px 8px;
  overflow: hidden;
  color: var(--atf-ink-soft);
  font-size: 11px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 7px;
  cursor: pointer;
  gap: 6px;
  transition:
    color 0.18s ease,
    background 0.18s ease,
    border-color 0.18s ease;
}

.composer__tool:hover:not(:disabled) {
  color: var(--atf-brand-deep);
  background: var(--atf-brand-soft);
  border-color: var(--atf-line);
}

.composer__tool:disabled {
  cursor: not-allowed;
  opacity: 0.42;
}

.composer__tool .el-icon {
  flex: none;
  font-size: 13px;
}

.composer__tool-label {
  flex: none;
  color: var(--atf-muted);
  font-size: 10px;
  letter-spacing: 0.04em;
}

.composer__tool-value {
  overflow: hidden;
  color: var(--atf-ink);
  font-size: 11px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer__tool-caret {
  flex: none;
  color: var(--atf-muted);
  font-size: 9px !important;
}

.composer__saving {
  flex: none;
  margin-left: 2px;
  color: var(--atf-muted);
  font-size: 9.5px;
}

.composer__toolbar-meta {
  display: inline-flex;
  flex: none;
  align-items: center;
  margin-left: auto;
  color: var(--atf-muted);
  font-size: 9.5px;
  gap: 5px;
}

.composer__toolbar-meta i {
  width: 5px;
  height: 5px;
  background: var(--atf-mint);
  border-radius: 50%;
}

/* ---------- 知识库与温度选择器 ---------- */
.kb-picker {
  color: var(--atf-ink-soft);
}

.kb-picker__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 0 2px;
  gap: 10px;
}

.kb-picker__head span {
  font-size: 12px;
  font-weight: 700;
}

.kb-picker__head small {
  color: var(--atf-muted);
  font-size: 9.5px;
}

.kb-picker__list {
  display: flex;
  flex-direction: column;
  max-height: 268px;
  padding: 4px 0;
  margin-top: 6px;
  overflow-y: auto;
  gap: 1px;
}

.kb-picker__item {
  display: flex;
  width: 100%;
  height: auto;
  padding: 7px 8px;
  margin: 0;
  border-radius: var(--atf-radius-sm);
  transition: background 0.16s ease;
}

.kb-picker__item:hover {
  background: var(--atf-bg);
}

.kb-picker__item.is-checked {
  background: var(--atf-brand-soft);
}

.kb-picker__item :deep(.el-checkbox__input) {
  margin-top: 1px;
}

.kb-picker__item :deep(.el-checkbox__label) {
  display: block;
  min-width: 0;
  padding-left: 10px;
}

.kb-picker__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 2px;
}

.kb-picker__name {
  display: flex;
  align-items: center;
  overflow: hidden;
  color: var(--atf-ink);
  font-size: 11.5px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
  gap: 5px;
}

.kb-picker__name em {
  flex: none;
  padding: 1px 4px;
  color: var(--atf-brand-deep);
  font-size: 8.5px;
  font-style: normal;
  border: 1px solid currentColor;
  border-radius: 1px;
}

.kb-picker__meta {
  color: var(--atf-muted);
  font-size: 9.5px;
}

.kb-picker__empty {
  padding: 16px 4px;
  color: var(--atf-muted);
  font-size: 11px;
  text-align: center;
}

.kb-picker__manage {
  display: block;
  padding-top: 8px;
  color: var(--atf-brand);
  font-size: 10.5px;
  font-weight: 650;
  text-align: right;
  border-top: 1px solid var(--atf-line);
}

.temp-picker__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 0 2px;
}

.temp-picker__head span {
  color: var(--atf-ink-soft);
  font-size: 12px;
  font-weight: 700;
}

.temp-picker__head b {
  color: var(--atf-brand-deep);
  font-size: 17px;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
}

.temp-picker :deep(.el-slider) {
  margin: 2px 5px 0;
}

.temp-picker :deep(.el-slider__bar) {
  background: var(--atf-bronze);
}

.temp-picker :deep(.el-slider__button) {
  border-color: var(--atf-bronze);
}

.temp-picker__presets {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
}

.temp-picker__presets button {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 6px 4px;
  color: var(--atf-muted);
  font-size: 9px;
  background: var(--atf-bg);
  border: 1px solid var(--atf-line);
  border-radius: 7px;
  cursor: pointer;
  gap: 1px;
  transition: all 0.16s ease;
}

.temp-picker__presets button b {
  color: var(--atf-ink-soft);
  font-size: 10.5px;
}

.temp-picker__presets button:hover {
  border-color: var(--atf-line-strong);
}

.temp-picker__presets button.is-active {
  background: var(--atf-brand-soft);
  border-color: var(--atf-brand);
}

.temp-picker__presets button.is-active b {
  color: var(--atf-brand-deep);
}

.temp-picker p {
  margin-top: 10px;
  color: var(--atf-muted);
  font-size: 9.5px;
  line-height: 1.6;
}

.composer__box {
  padding: 13px 12px 9px 16px;
  background: var(--atf-surface-raised);
  border: 1px solid var(--atf-line-strong);
  border-radius: var(--atf-radius-sm);
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease;
}

.composer__box:focus-within {
  border-color: var(--atf-ink-soft);
  box-shadow: none;
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
  color: var(--atf-placeholder);
}

.composer__actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 7px;
}

.composer__hint {
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 9px;
  font-variant-numeric: tabular-nums;
}

.composer__send {
  height: 28px;
  padding: 0 1px 2px;
  color: var(--atf-ink);
  font-size: 12px;
  letter-spacing: 0.08em;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--atf-brand);
  border-radius: 0 !important;
  box-shadow: none;
}

.composer__send:hover:not(.is-disabled) {
  color: var(--atf-brand);
  background: transparent;
  border-bottom-color: var(--atf-brand);
}

.composer__send.is-disabled,
.composer__send.is-disabled:hover {
  color: var(--atf-placeholder);
  background: transparent;
  border-bottom-color: var(--atf-line);
}

.composer__send-arrow {
  margin-left: 4px;
  font-size: 12px;
  line-height: 1;
}

.composer__stop {
  width: 33px;
  height: 33px;
  color: var(--atf-brand);
  background: var(--atf-brand-soft);
  border: 0;
  border-radius: 1px !important;
}

.composer__legal {
  margin-top: 8px;
  color: var(--atf-muted);
  font-size: 9px;
  letter-spacing: 0.02em;
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
    padding: 8px 14px 12px;
  }

  .composer__toolbar-meta {
    display: none;
  }

  .composer__tool-value {
    max-width: 140px;
  }

  .chat-welcome {
    padding-top: 2vh;
  }

  .chat-welcome__samples {
    margin-top: 26px;
  }

  .sample-row {
    grid-template-columns: 28px minmax(0, 1fr) 18px;
    padding: 13px 2px;
    gap: 10px;
  }

  .sample-row__body small {
    white-space: normal;
  }

  .chat__thread {
    gap: 20px;
  }
}
</style>
