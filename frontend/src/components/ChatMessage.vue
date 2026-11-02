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
// 只展示判定来源，不暴露分类概率；概率仍保留在接口与运营数据中。
const intentDetail = computed(
  () => INTENT_SOURCE_LABEL[props.message.intent_source] || '',
)
const showNoSource = computed(
  () =>
    !props.streaming &&
    props.message.role === 'assistant' &&
    props.message.intent &&
    props.message.intent !== 'greeting' &&
    !sources.value.length,
)
const sourceSummary = computed(() => {
  const titles = [...new Set(sources.value.map((item) => item.title).filter(Boolean))]
  if (!titles.length) return ''
  const head = titles.slice(0, 2).join(' · ')
  return titles.length > 2 ? `${head} 等 ${titles.length} 篇` : head
})
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
    <div class="msg__bot-avatar" aria-hidden="true">A</div>

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
          <span class="msg__sources-label">
            <el-icon><Document /></el-icon>
            <span class="msg__sources-text">
              出处<template v-if="sourceSummary"> · {{ sourceSummary }}</template>
            </span>
          </span>
          <span class="msg__sources-count">
            {{ String(sources.length).padStart(2, '0') }}
            <el-icon class="msg__sources-arrow">
              <ArrowDown v-if="!showSources" />
              <ArrowUp v-else />
            </el-icon>
          </span>
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
  gap: 13px;
}

.msg__user-avatar,
.msg__bot-avatar {
  display: grid;
  flex: none;
  border-radius: 1px;
  place-items: center;
}

.msg__user-avatar {
  width: 30px;
  height: 30px;
  margin-top: 2px;
  color: var(--atf-bg);
  font-family: var(--atf-font-display);
  font-size: 12px;
  font-weight: 600;
  background: var(--atf-ink);
}

.msg__bot-avatar {
  width: 30px;
  height: 30px;
  color: var(--atf-on-brand);
  font-family: var(--atf-font-display);
  font-size: 14px;
  font-weight: 500;
  background: var(--atf-brand);
}

.msg__user-bubble {
  max-width: min(620px, 76%);
  padding: 12px 16px;
  color: var(--atf-ink);
  font-size: 13.5px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--atf-brand-soft);
  border-radius: 2px 2px 0 2px;
}

.msg__body {
  flex: 1;
  min-width: 0;
  max-width: min(720px, 88%);
}

.msg__meta {
  display: flex;
  align-items: center;
  min-height: 24px;
  margin-bottom: 10px;
  gap: 10px;
}

.msg__author {
  color: var(--atf-ink);
  font-family: var(--atf-font-display);
  font-size: 12.5px;
  font-weight: 600;
}

.msg__intent {
  padding: 0 0 1px;
  font-family: var(--atf-font-mono);
  font-size: 8.5px;
  font-weight: 500;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  border-bottom: 1px dotted currentColor;
  border-radius: 0;
}

.tone-violet {
  color: var(--atf-violet);
}

.tone-blue {
  color: var(--atf-tone-blue);
}

.tone-mint {
  color: var(--atf-mint);
}

.tone-orange {
  color: var(--atf-tone-gold);
}

.tone-red {
  color: var(--atf-brand);
}

.tone-pink {
  color: var(--atf-tone-pink);
}

.tone-gold {
  color: var(--atf-tone-gold);
}

.tone-teal {
  color: var(--atf-tone-teal);
}

.tone-gray {
  color: var(--atf-muted);
}

.msg__intent-detail {
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 8.5px;
  font-variant-numeric: tabular-nums;
  cursor: help;
}

.msg__latency {
  margin-left: auto;
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 9px;
  font-variant-numeric: tabular-nums;
}

/* 助手回答不做卡片：一条细线起头，像编辑栏里的正文 */
.msg__bubble {
  padding: 18px 0 0;
  background: transparent;
  border: 0;
  border-top: 1px solid var(--atf-line);
  border-radius: 0;
}

.msg__bubble.is-error {
  border-top-color: var(--atf-brand-line);
}

.msg__typing {
  display: flex;
  align-items: center;
  color: var(--atf-muted);
  font-size: 12px;
  gap: 9px;
}

.msg__typing-dots {
  display: inline-flex;
  gap: 3px;
}

.msg__typing-dots i {
  width: 5px;
  height: 5px;
  background: var(--atf-brand);
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
    opacity: 0.3;
    transform: translateY(0);
  }
  30% {
    opacity: 1;
    transform: translateY(-3px);
  }
}

.msg__cursor {
  display: inline-block;
  width: 1px;
  height: 15px;
  margin-left: 3px;
  vertical-align: -2px;
  background: var(--atf-brand);
  animation: cursor-blink 0.9s steps(2, start) infinite;
}

@keyframes cursor-blink {
  50% {
    opacity: 0;
  }
}

.msg__error {
  padding-top: 10px;
  margin-top: 10px;
  color: var(--atf-brand-deep);
  font-size: 12px;
  line-height: 1.65;
  border-top: 1px solid var(--atf-brand-line-soft);
}

.msg__actions {
  display: flex;
  align-items: center;
  height: 28px;
  margin-top: 7px;
  gap: 14px;
  opacity: 0;
  transition: opacity 0.2s ease;
}

.msg--assistant:hover .msg__actions {
  opacity: 1;
}

.msg__action {
  display: inline-flex;
  align-items: center;
  padding: 3px 0;
  color: var(--atf-muted);
  font-size: 10px;
  background: transparent;
  border: 0;
  cursor: pointer;
  gap: 4px;
}

.msg__action:hover {
  color: var(--atf-brand);
  text-decoration: underline;
  text-underline-offset: 3px;
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
  margin-top: 9px;
  color: var(--atf-tone-gold);
  font-size: 10.5px;
  line-height: 1.65;
  background: var(--atf-tone-peach-bg);
  border-left: 2px solid var(--atf-peach);
  border-radius: 0;
  gap: 6px;
}

.msg__sources {
  margin-top: 10px;
}

/* 出处收成回答末尾的一行脚注：始终露出前两篇资料名，点击再展开全文 */
.msg__sources-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 9px 1px 0;
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 9.5px;
  letter-spacing: 0.06em;
  text-align: left;
  background: transparent;
  border: 0;
  border-top: 1px solid var(--atf-line-strong);
  border-radius: 0;
  cursor: pointer;
  transition: color 0.16s ease;
  gap: 12px;
}

.msg__sources-label {
  display: flex;
  flex: 1;
  align-items: center;
  min-width: 0;
  gap: 6px;
}

.msg__sources-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.msg__sources-count {
  display: inline-flex;
  flex: none;
  align-items: center;
  color: var(--atf-brand);
  letter-spacing: 0.1em;
  gap: 5px;
}

.msg__sources-toggle:hover,
.msg__sources-toggle.is-open {
  color: var(--atf-ink-soft);
}

.msg__sources-toggle:hover .msg__sources-count,
.msg__sources-toggle.is-open .msg__sources-count {
  color: var(--atf-brand-deep);
}

.msg__sources-arrow {
  font-size: 9px;
}

.msg__source-list {
  display: flex;
  flex-direction: column;
  margin-top: 10px;
  border-top: 1px solid var(--atf-line-strong);
}

.msg__source {
  padding: 11px 1px;
  background: transparent;
  border-bottom: 1px solid var(--atf-line);
  border-radius: 0;
}

.msg__source-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.msg__source-index {
  flex: none;
  color: var(--atf-brand);
  font-family: var(--atf-font-display);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}

.msg__source-title {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  gap: 2px;
}

.msg__source-title strong {
  overflow: hidden;
  color: var(--atf-ink);
  font-size: 11px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.msg__source-title span {
  color: var(--atf-muted);
  font-size: 9.5px;
}

.msg__source-score {
  flex: none;
  color: var(--atf-bronze);
  font-family: var(--atf-font-mono);
  font-size: 9px;
  font-variant-numeric: tabular-nums;
}

.msg__source-content {
  display: -webkit-box;
  margin-top: 7px;
  overflow: hidden;
  color: var(--atf-muted);
  font-size: 10.5px;
  line-height: 1.7;
  -webkit-box-orient: vertical;
  /* 后端给的已是相关摘录，放宽到 6 行避免把摘录尾部（常是答案）裁掉 */
  -webkit-line-clamp: 6;
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
