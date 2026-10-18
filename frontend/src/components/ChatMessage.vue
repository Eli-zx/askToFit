<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowDown, ArrowUp, CopyDocument, Document, MagicStick } from '@element-plus/icons-vue'

import { INTENT_META } from '@/utils/constants'
import { formatLatency } from '@/utils/format'
import { renderMarkdown } from '@/utils/markdown'

const props = defineProps({
  message: { type: Object, required: true },
  question: { type: String, default: '' },
  streaming: { type: Boolean, default: false },
})

const showSources = ref(false)

const html = computed(() => renderMarkdown(props.message.content))
const sources = computed(() => props.message.sources || [])
const intent = computed(() => INTENT_META[props.message.intent] || null)
const INTENT_SOURCE_LABEL = { bert: 'BERT', keyword: '关键词', default: '默认' }
const intentDetail = computed(() => {
  const source = INTENT_SOURCE_LABEL[props.message.intent_source]
  if (!source) return ''
  const confidence = Number(props.message.intent_confidence)
  return Number.isFinite(confidence) && confidence > 0
    ? `${source} ${Math.round(confidence * 100)}%`
    : source
})
const showNoSource = computed(
  () =>
    !props.streaming &&
    props.message.role === 'assistant' &&
    props.message.intent &&
    props.message.intent !== 'greeting' &&
    !sources.value.length,
)
const rewrite = computed(() => {
  const rewritten = props.message.rewritten_query
  if (!rewritten || !props.question || rewritten === props.question) return ''
  return rewritten
})

const stageText = computed(() => {
  const stage = props.message.stage
  if (stage === 'rewriting') return '正在理解上下文'
  if (stage === 'retrieving') return '正在检索知识库'
  return '正在组织回答'
})

async function copyContent() {
  try {
    await navigator.clipboard.writeText(props.message.content || '')
    ElMessage.success('已复制回答内容')
  } catch {
    ElMessage.warning('复制失败，请手动选择内容')
  }
}
</script>

<template>
  <article v-if="message.role === 'user'" class="msg msg--user">
    <div class="msg__user-bubble">{{ message.content }}</div>
    <div class="msg__user-avatar">
      {{ (message.authorInitial || '我').slice(0, 1) }}
    </div>
  </article>

  <article v-else class="msg msg--assistant">
    <div class="msg__bot-avatar">
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M12 4c.8 3 2.3 4.5 5.3 5.3-3 .8-4.5 2.3-5.3 5.3-.8-3-2.3-4.5-5.3-5.3 3-.8 4.5-2.3 5.3-5.3Z"
          fill="currentColor"
        />
        <circle cx="17.8" cy="5.6" r="1.3" fill="currentColor" opacity=".6" />
      </svg>
    </div>

    <div class="msg__body">
      <div class="msg__meta">
        <span class="msg__author">AskToFit 导购</span>
        <span v-if="intent" class="msg__intent" :class="`tone-${intent.tone}`">
          {{ intent.label }}
        </span>
        <el-tooltip
          v-if="intentDetail"
          placement="top"
          :show-after="150"
        >
          <template #content>
            意图由 {{ message.intent_source === 'bert' ? '本地 BERT 模型' : '关键词规则' }} 判定
          </template>
          <span class="msg__intent-detail">{{ intentDetail }}</span>
        </el-tooltip>
        <span v-if="message.latency_ms" class="msg__latency">
          {{ formatLatency(message.latency_ms) }}
        </span>
      </div>

      <div class="msg__bubble" :class="{ 'is-error': message.error }">
        <div v-if="!message.content && streaming" class="msg__typing">
          <span class="msg__typing-dots"><i /><i /><i /></span>
          {{ stageText }}
        </div>
        <div v-else class="markdown-body" v-html="html" />
        <span v-if="streaming && message.content" class="msg__cursor" />
        <p v-if="message.error" class="msg__error">{{ message.error }}</p>
      </div>

      <div v-if="!streaming && message.content" class="msg__actions">
        <button type="button" class="msg__action" @click="copyContent">
          <el-icon><CopyDocument /></el-icon>
          复制
        </button>
        <el-tooltip v-if="rewrite" placement="top" :show-after="120">
          <template #content>
            <span class="msg__rewrite-tip">检索改写：{{ rewrite }}</span>
          </template>
          <span class="msg__action msg__action--static">
            <el-icon><MagicStick /></el-icon>
            检索改写
          </span>
        </el-tooltip>
      </div>

      <p v-if="showNoSource" class="msg__no-source">
        <el-icon><Document /></el-icon>
        本轮知识库未命中可靠资料，回答基于通用服务规范，建议向人工客服复核。
      </p>

      <div v-if="sources.length" class="msg__sources">
        <button
          type="button"
          class="msg__sources-toggle"
          :class="{ 'is-open': showSources }"
          @click="showSources = !showSources"
        >
          <el-icon><Document /></el-icon>
          引用 {{ sources.length }} 条知识库资料
          <el-icon class="msg__sources-arrow">
            <ArrowDown v-if="!showSources" />
            <ArrowUp v-else />
          </el-icon>
        </button>

        <Transition name="source-expand">
          <div v-if="showSources" class="msg__source-list">
            <div
              v-for="(source, index) in sources"
              :key="source.chunk_uid || index"
              class="msg__source"
            >
              <div class="msg__source-head">
                <span class="msg__source-index">{{ String(index + 1).padStart(2, '0') }}</span>
                <div class="msg__source-title">
                  <strong>{{ source.title || '未命名文档' }}</strong>
                  <span>
                    {{ source.section || '正文' }}
                    <template v-if="source.page"> · 第 {{ source.page }} 页</template>
                  </span>
                </div>
                <span class="msg__source-score">
                  {{ Number(source.score || 0).toFixed(3) }}
                </span>
              </div>
              <p class="msg__source-content">{{ source.content }}</p>
            </div>
          </div>
        </Transition>
      </div>
    </div>
  </article>
</template>

<style scoped>
.msg {
  display: flex;
  width: 100%;
  animation: atf-rise 0.32s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.msg--user {
  justify-content: flex-end;
  gap: 10px;
}

.msg--assistant {
  align-items: flex-start;
  gap: 12px;
}

.msg__user-avatar,
.msg__bot-avatar {
  display: grid;
  flex: none;
  color: #fff;
  border-radius: 12px;
  place-items: center;
}

.msg__user-avatar {
  width: 32px;
  height: 32px;
  margin-top: 2px;
  font-size: 12px;
  font-weight: 700;
  background: linear-gradient(135deg, #39395f, #65658f);
}

.msg__bot-avatar {
  width: 34px;
  height: 34px;
  background: linear-gradient(135deg, #5b5bd6, #8b7cf6 60%, #2dd4bf 140%);
  box-shadow: 0 6px 14px rgba(91, 91, 214, 0.22);
}

.msg__bot-avatar svg {
  width: 76%;
  height: 76%;
}

.msg__user-bubble {
  max-width: min(640px, 76%);
  padding: 12px 17px;
  color: #fff;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  background: linear-gradient(135deg, #5b5bd6, #7770e8);
  border-radius: 18px 18px 5px 18px;
  box-shadow: 0 8px 22px rgba(91, 91, 214, 0.2);
}

.msg__body {
  flex: 1;
  min-width: 0;
  max-width: min(820px, 88%);
}

.msg__meta {
  display: flex;
  align-items: center;
  height: 24px;
  margin-bottom: 5px;
  gap: 9px;
}

.msg__author {
  color: var(--atf-ink-soft);
  font-size: 12px;
  font-weight: 700;
}

.msg__intent {
  padding: 2px 8px;
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.04em;
  border-radius: 999px;
}

.tone-violet {
  color: #5b4bc4;
  background: #eeebff;
}

.tone-blue {
  color: #2563a8;
  background: #e8f2ff;
}

.tone-mint {
  color: #137d6d;
  background: #e3faf5;
}

.tone-orange {
  color: #a86118;
  background: #fff3e4;
}

.tone-red {
  color: #b03a47;
  background: #ffecef;
}

.tone-pink {
  color: #a63c72;
  background: #ffeaf4;
}

.tone-gold {
  color: #916612;
  background: #fdf5dd;
}

.tone-teal {
  color: #16697a;
  background: #e5f8fb;
}

.tone-gray {
  color: #61657d;
  background: #f1f2f7;
}

.msg__intent-detail {
  color: #a0a3b8;
  font-size: 9px;
  font-variant-numeric: tabular-nums;
  cursor: help;
}

.msg__latency {
  color: #aeb1c5;
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}

.msg__bubble {
  padding: 15px 18px;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 5px 18px 18px 18px;
  box-shadow: 0 4px 18px rgba(43, 38, 95, 0.045);
}

.msg__bubble.is-error {
  border-color: rgba(220, 76, 95, 0.22);
}

.msg__typing {
  display: flex;
  align-items: center;
  color: var(--atf-muted);
  font-size: 12.5px;
  gap: 9px;
}

.msg__typing-dots {
  display: inline-flex;
  gap: 3px;
}

.msg__typing-dots i {
  width: 5px;
  height: 5px;
  background: var(--atf-violet);
  border-radius: 50%;
  animation: typing-bounce 1.1s ease-in-out infinite;
}

.msg__typing-dots i:nth-child(2) {
  animation-delay: 0.15s;
}

.msg__typing-dots i:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes typing-bounce {
  0%,
  60%,
  100% {
    opacity: 0.35;
    transform: translateY(0);
  }
  30% {
    opacity: 1;
    transform: translateY(-4px);
  }
}

.msg__cursor {
  display: inline-block;
  width: 2px;
  height: 15px;
  margin-left: 2px;
  vertical-align: -2px;
  background: var(--atf-brand);
  border-radius: 2px;
  animation: cursor-blink 0.9s steps(2, start) infinite;
}

@keyframes cursor-blink {
  50% {
    opacity: 0;
  }
}

.msg__error {
  margin-top: 10px;
  padding-top: 10px;
  color: #c24151;
  font-size: 12px;
  line-height: 1.6;
  border-top: 1px dashed rgba(194, 65, 81, 0.24);
}

.msg__actions {
  display: flex;
  align-items: center;
  height: 28px;
  margin-top: 6px;
  gap: 6px;
  opacity: 0;
  transition: opacity 0.2s ease;
}

.msg--assistant:hover .msg__actions {
  opacity: 1;
}

.msg__action {
  display: inline-flex;
  align-items: center;
  padding: 4px 8px;
  color: var(--atf-muted);
  font-size: 10.5px;
  background: transparent;
  border: 0;
  border-radius: 7px;
  cursor: pointer;
  gap: 4px;
}

.msg__action:hover {
  color: var(--atf-brand);
  background: var(--atf-brand-soft);
}

.msg__action--static {
  cursor: help;
}

.msg__rewrite-tip {
  font-size: 11px;
  line-height: 1.6;
}

.msg__no-source {
  display: flex;
  align-items: flex-start;
  padding: 8px 11px;
  margin-top: 8px;
  color: #9a6a20;
  font-size: 10.5px;
  line-height: 1.6;
  background: #fffaf0;
  border: 1px solid rgba(202, 138, 4, 0.14);
  border-radius: 9px;
  gap: 6px;
}

.msg__sources {
  margin-top: 9px;
}

.msg__sources-toggle {
  display: inline-flex;
  align-items: center;
  padding: 6px 11px;
  color: var(--atf-brand-deep);
  font-size: 10.5px;
  font-weight: 650;
  background: #f4f3ff;
  border: 1px solid rgba(91, 91, 214, 0.1);
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.2s ease;
  gap: 5px;
}

.msg__sources-toggle:hover,
.msg__sources-toggle.is-open {
  background: #ecebfe;
}

.msg__sources-arrow {
  font-size: 10px;
}

.msg__source-list {
  display: flex;
  flex-direction: column;
  margin-top: 8px;
  gap: 7px;
}

.msg__source {
  padding: 11px 13px;
  background: #fbfbff;
  border: 1px solid var(--atf-line);
  border-radius: 11px;
}

.msg__source-head {
  display: flex;
  align-items: center;
  gap: 9px;
}

.msg__source-index {
  display: grid;
  flex: none;
  width: 22px;
  height: 22px;
  color: var(--atf-brand);
  font-size: 9px;
  font-weight: 750;
  background: var(--atf-brand-soft);
  border-radius: 7px;
  place-items: center;
}

.msg__source-title {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  gap: 1px;
}

.msg__source-title strong {
  overflow: hidden;
  color: var(--atf-ink-soft);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.msg__source-title span {
  color: var(--atf-muted);
  font-size: 9.5px;
}

.msg__source-score {
  flex: none;
  color: #2f8f73;
  font-size: 9.5px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.msg__source-content {
  display: -webkit-box;
  margin-top: 7px;
  overflow: hidden;
  color: #6f7288;
  font-size: 10.5px;
  line-height: 1.65;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 4;
}

.source-expand-enter-active,
.source-expand-leave-active {
  transition: all 0.25s ease;
}

.source-expand-enter-from,
.source-expand-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

@media (max-width: 720px) {
  .msg__user-bubble {
    max-width: 84%;
  }

  .msg__body {
    max-width: calc(100% - 44px);
  }

  .msg__bubble {
    padding: 13px 14px;
  }
}
</style>
