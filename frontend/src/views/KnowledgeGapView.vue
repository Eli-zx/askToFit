<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, Close, Refresh, Search, Tickets } from '@element-plus/icons-vue'

import {
  fetchKnowledgeGapStats,
  listKnowledgeGaps,
  updateKnowledgeGap,
} from '@/api/chat'
import { INTENT_META } from '@/utils/constants'
import { toErrorMessage } from '@/utils/errors'
import { formatDateTime, formatScore } from '@/utils/format'

const PAGE_SIZE = 20

const loading = ref(false)
const statsLoading = ref(false)
const items = ref([])
const stats = ref(null)
const page = ref(1)
const hasNext = ref(false)
const noteDialog = ref(false)
const noteTarget = ref(null)
const noteText = ref('')
const saving = ref(false)

const filters = reactive({
  status: 'open',
  intent: '',
  q: '',
})

const STATUS_META = {
  open: { label: '待处理', type: 'warning' },
  resolved: { label: '已解决', type: 'success' },
  ignored: { label: '已忽略', type: 'info' },
}

const intentOptions = computed(() =>
  Object.entries(INTENT_META).map(([value, meta]) => ({
    value,
    label: meta.label,
  })),
)

const statsCards = computed(() => {
  const data = stats.value
  if (!data) return []
  return [
    { label: '缺口总数', value: data.total, tone: 'violet' },
    { label: '待处理', value: data.open, tone: 'orange' },
    { label: '已解决', value: data.resolved, tone: 'mint' },
    { label: '已忽略', value: data.ignored, tone: 'gray' },
  ]
})

const trendMax = computed(() =>
  Math.max(1, ...(stats.value?.by_date || []).map((item) => item.count)),
)

function intentLabel(intent) {
  return INTENT_META[intent]?.label || '综合咨询'
}

function intentTone(intent) {
  return INTENT_META[intent]?.tone || 'gray'
}

function sourceLabel(source) {
  return { bert: 'BERT', keyword: '关键词', default: '默认' }[source] || '—'
}

function confidenceText(item) {
  const confidence = Number(item.intent_confidence)
  return Number.isFinite(confidence) && confidence > 0
    ? `${Math.round(confidence * 100)}%`
    : '—'
}

async function loadGaps() {
  loading.value = true
  try {
    const data = await listKnowledgeGaps({
      limit: PAGE_SIZE,
      offset: (page.value - 1) * PAGE_SIZE,
      status: filters.status,
      intent: filters.intent || undefined,
      q: filters.q.trim() || undefined,
    })
    items.value = data
    hasNext.value = data.length === PAGE_SIZE
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '知识缺口加载失败'))
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  statsLoading.value = true
  try {
    stats.value = await fetchKnowledgeGapStats(14)
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '统计信息加载失败'))
  } finally {
    statsLoading.value = false
  }
}

async function refresh() {
  await Promise.all([loadGaps(), loadStats()])
}

function applyFilters() {
  page.value = 1
  loadGaps()
}

function resetFilters() {
  filters.status = 'open'
  filters.intent = ''
  filters.q = ''
  applyFilters()
}

function previousPage() {
  if (page.value <= 1) return
  page.value -= 1
  loadGaps()
}

function nextPage() {
  if (!hasNext.value) return
  page.value += 1
  loadGaps()
}

async function setStatus(item, status) {
  try {
    await updateKnowledgeGap(item.id, { status })
    ElMessage.success(status === 'resolved' ? '已标记为解决' : status === 'ignored' ? '已忽略该缺口' : '已重新打开')
    await refresh()
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '状态更新失败'))
  }
}

function openNote(item) {
  noteTarget.value = item
  noteText.value = item.note || ''
  noteDialog.value = true
}

async function saveNote() {
  if (!noteTarget.value) return
  saving.value = true
  try {
    await updateKnowledgeGap(noteTarget.value.id, { note: noteText.value })
    ElMessage.success('备注已保存')
    noteDialog.value = false
    await loadGaps()
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '备注保存失败'))
  } finally {
    saving.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <div class="atf-page">
    <div class="atf-page-inner">
      <header class="gap-head">
        <div>
          <span class="atf-eyebrow">Knowledge gap ops</span>
          <h1 class="atf-title">
            知识缺口<span class="atf-gradient-text">运营台</span>
          </h1>
          <p class="atf-subtitle">
            检索未命中可靠资料的问题会自动沉淀在这里。处理完成后可标记状态并记录补充说明。
          </p>
        </div>
        <el-button :loading="loading || statsLoading" @click="refresh">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
      </header>

      <section class="gap-stats">
        <article
          v-for="card in statsCards"
          :key="card.label"
          class="gap-stat"
          :class="`gap-stat--${card.tone}`"
        >
          <span>{{ card.label }}</span>
          <strong>{{ card.value }}</strong>
        </article>
        <el-skeleton v-if="!statsCards.length" :rows="1" animated />
      </section>

      <section v-if="stats?.by_date?.length" class="gap-trend">
        <div class="gap-trend__head">
          <strong>近 14 天新增</strong>
          <span>共 {{ stats.by_date.reduce((sum, item) => sum + item.count, 0) }} 条</span>
        </div>
        <div class="gap-trend__bars">
          <div
            v-for="item in stats.by_date"
            :key="item.key"
            class="gap-trend__bar"
            :title="`${item.key}：${item.count} 条`"
          >
            <i :style="{ height: `${Math.max(6, (item.count / trendMax) * 100)}%` }" />
            <small>{{ item.key.slice(5) }}</small>
          </div>
        </div>
      </section>

      <section class="gap-filters">
        <el-select v-model="filters.status" aria-label="状态筛选" @change="applyFilters">
          <el-option label="全部状态" value="all" />
          <el-option label="待处理" value="open" />
          <el-option label="已解决" value="resolved" />
          <el-option label="已忽略" value="ignored" />
        </el-select>
        <el-select
          v-model="filters.intent"
          clearable
          placeholder="全部意图"
          aria-label="意图筛选"
          @change="applyFilters"
        >
          <el-option
            v-for="option in intentOptions"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-input
          v-model="filters.q"
          clearable
          placeholder="搜索问题关键词"
          @keyup.enter="applyFilters"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <el-button type="primary" @click="applyFilters">筛选</el-button>
        <el-button text @click="resetFilters">重置</el-button>
      </section>

      <section v-loading="loading" class="gap-list">
        <div v-if="!items.length && !loading" class="gap-empty">
          <div class="gap-empty__icon">
            <el-icon><Tickets /></el-icon>
          </div>
          <strong>当前筛选下没有知识缺口</strong>
          <span>调整筛选条件，或等待新的未命中问题沉淀</span>
        </div>

        <article v-for="item in items" :key="item.id" class="gap-card">
          <div class="gap-card__head">
            <div class="gap-card__tags">
              <span class="gap-card__intent" :class="`tone-${intentTone(item.intent)}`">
                {{ intentLabel(item.intent) }}
              </span>
              <span class="gap-card__source">
                {{ sourceLabel(item.intent_source) }} · {{ confidenceText(item) }}
              </span>
              <el-tag :type="STATUS_META[item.status]?.type || 'info'" size="small" effect="light">
                {{ STATUS_META[item.status]?.label || item.status }}
              </el-tag>
            </div>
            <span class="gap-card__time">{{ formatDateTime(item.created_at) }}</span>
          </div>

          <p class="gap-card__question">{{ item.question }}</p>

          <div class="gap-card__scores">
            <span>最高分 {{ formatScore(item.best_score) }}</span>
            <span>向量 {{ formatScore(item.vector_score) }}</span>
            <span>重排 {{ formatScore(item.rerank_score) }}</span>
            <span>模式 {{ item.retrieval_mode || '—' }}</span>
            <span>知识库 {{ (item.kb_ids || []).join(' / ') || '—' }}</span>
          </div>

          <p v-if="item.note" class="gap-card__note">{{ item.note }}</p>

          <div class="gap-card__actions">
            <el-button
              v-if="item.status !== 'resolved'"
              size="small"
              type="success"
              plain
              @click="setStatus(item, 'resolved')"
            >
              <el-icon><Check /></el-icon>
              标记已解决
            </el-button>
            <el-button
              v-if="item.status !== 'ignored'"
              size="small"
              plain
              @click="setStatus(item, 'ignored')"
            >
              <el-icon><Close /></el-icon>
              忽略
            </el-button>
            <el-button
              v-if="item.status !== 'open'"
              size="small"
              plain
              type="warning"
              @click="setStatus(item, 'open')"
            >
              重新打开
            </el-button>
            <el-button size="small" text @click="openNote(item)">
              {{ item.note ? '编辑备注' : '添加备注' }}
            </el-button>
          </div>
        </article>
      </section>

      <div class="gap-pager">
        <span>第 {{ page }} 页</span>
        <el-button size="small" :disabled="page <= 1" @click="previousPage">上一页</el-button>
        <el-button size="small" :disabled="!hasNext" @click="nextPage">下一页</el-button>
      </div>
    </div>

    <el-dialog v-model="noteDialog" title="缺口备注" width="520px" append-to-body>
      <el-input
        v-model="noteText"
        type="textarea"
        :rows="5"
        maxlength="1000"
        show-word-limit
        placeholder="记录补充的文档、处理结论或后续计划…"
      />
      <template #footer>
        <el-button @click="noteDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveNote">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.gap-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 24px;
  gap: 18px;
}

.gap-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.gap-stat {
  display: flex;
  flex-direction: column;
  padding: 16px 18px;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 15px;
  box-shadow: var(--atf-shadow-sm);
  gap: 8px;
}

.gap-stat span {
  color: var(--atf-muted);
  font-size: 11px;
  font-weight: 650;
}

.gap-stat strong {
  color: var(--atf-ink);
  font-size: 26px;
  font-weight: 760;
  font-variant-numeric: tabular-nums;
}

.gap-stat--orange strong {
  color: #b06a1c;
}

.gap-stat--mint strong {
  color: #1d8a70;
}

.gap-stat--gray strong {
  color: #6b6f86;
}

.gap-stat--violet strong {
  color: var(--atf-brand-deep);
}

.gap-trend {
  padding: 16px 18px;
  margin-top: 14px;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 15px;
}

.gap-trend__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--atf-ink-soft);
  font-size: 12px;
}

.gap-trend__head span {
  color: var(--atf-muted);
  font-size: 10.5px;
}

.gap-trend__bars {
  display: flex;
  align-items: flex-end;
  height: 90px;
  margin-top: 12px;
  gap: 8px;
}

.gap-trend__bar {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  height: 100%;
  gap: 6px;
}

.gap-trend__bar i {
  width: 100%;
  max-width: 34px;
  background: linear-gradient(180deg, #8b7cf6, #5b5bd6);
  border-radius: 6px 6px 3px 3px;
  opacity: 0.82;
}

.gap-trend__bar small {
  color: #aeb1c5;
  font-size: 8.5px;
  white-space: nowrap;
}

.gap-filters {
  display: flex;
  align-items: center;
  padding: 12px;
  margin: 16px 0;
  background: #fbfaff;
  border: 1px solid var(--atf-line);
  border-radius: 13px;
  gap: 10px;
}

.gap-filters :deep(.el-select) {
  width: 140px;
}

.gap-filters :deep(.el-input) {
  max-width: 260px;
}

.gap-list {
  display: flex;
  flex-direction: column;
  min-height: 160px;
  gap: 10px;
}

.gap-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 60px 20px;
  color: var(--atf-muted);
  text-align: center;
  gap: 6px;
}

.gap-empty__icon {
  display: grid;
  width: 46px;
  height: 46px;
  margin-bottom: 6px;
  color: var(--atf-brand);
  font-size: 20px;
  background: var(--atf-brand-soft);
  border-radius: 50%;
  place-items: center;
}

.gap-empty strong {
  color: var(--atf-ink-soft);
  font-size: 13px;
}

.gap-empty span {
  font-size: 11px;
}

.gap-card {
  padding: 15px 17px;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 14px;
  box-shadow: var(--atf-shadow-sm);
}

.gap-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.gap-card__tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
}

.gap-card__intent {
  padding: 2px 9px;
  font-size: 9.5px;
  font-weight: 700;
  border-radius: 999px;
}

.tone-violet { color: #5b4bc4; background: #eeebff; }
.tone-blue { color: #2563a8; background: #e8f2ff; }
.tone-mint { color: #137d6d; background: #e3faf5; }
.tone-orange { color: #a86118; background: #fff3e4; }
.tone-red { color: #b03a47; background: #ffecef; }
.tone-pink { color: #a63c72; background: #ffeaf4; }
.tone-gold { color: #916612; background: #fdf5dd; }
.tone-teal { color: #16697a; background: #e5f8fb; }
.tone-gray { color: #61657d; background: #f1f2f7; }

.gap-card__source {
  color: #9a9db2;
  font-size: 9.5px;
  font-variant-numeric: tabular-nums;
}

.gap-card__time {
  flex: none;
  color: #aeb1c5;
  font-size: 9.5px;
}

.gap-card__question {
  margin-top: 10px;
  color: var(--atf-ink-soft);
  font-size: 13.5px;
  font-weight: 650;
  line-height: 1.65;
}

.gap-card__scores {
  display: flex;
  flex-wrap: wrap;
  margin-top: 8px;
  color: #8e91a8;
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  gap: 12px;
}

.gap-card__note {
  padding: 8px 11px;
  margin-top: 9px;
  color: #6d7088;
  font-size: 11px;
  line-height: 1.65;
  background: #f7f7fc;
  border-radius: 9px;
}

.gap-card__actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  margin-top: 11px;
  gap: 7px;
}

.gap-pager {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  margin-top: 16px;
  color: var(--atf-muted);
  font-size: 11px;
  gap: 8px;
}

@media (max-width: 860px) {
  .gap-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .gap-filters {
    flex-wrap: wrap;
  }
}
</style>
