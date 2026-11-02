<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, Close, Refresh, Search } from '@element-plus/icons-vue'

import {
  fetchKnowledgeGapStats,
  listKnowledgeGaps,
  updateKnowledgeGap,
} from '@/api/chat'
import { INTENT_META } from '@/utils/constants'
import { toErrorMessage } from '@/utils/errors'
import { formatDateTime, formatScore } from '@/utils/format'
import { groupGapsByUser } from '@/utils/knowledge-gaps'

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
  userId: null,
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

// 按最近 14 个自然日补齐没有缺口的日期，避免柱状图把稀疏数据画成等距分布
const trendDays = computed(() => {
  const byDate = new Map(
    (stats.value?.by_date || []).map((item) => [item.key, item.count]),
  )
  const now = new Date()
  return Array.from({ length: 14 }, (_, index) => {
    const date = new Date(
      now.getFullYear(),
      now.getMonth(),
      now.getDate() - (13 - index),
    )
    const key = [
      date.getFullYear(),
      String(date.getMonth() + 1).padStart(2, '0'),
      String(date.getDate()).padStart(2, '0'),
    ].join('-')
    return { key, count: byDate.get(key) || 0 }
  })
})

const trendTotal = computed(() =>
  (stats.value?.by_date || []).reduce((sum, item) => sum + item.count, 0),
)

const trendMax = computed(() =>
  Math.max(1, ...trendDays.value.map((item) => item.count)),
)

const trendHalf = computed(() => Math.round(trendMax.value / 2))

const userOptions = computed(() =>
  (stats.value?.by_user || []).map((user) => ({
    value: user.user_id,
    label: `${user.username}（${user.total}）`,
  })),
)

const userTotalMap = computed(() => {
  const map = {}
  for (const user of stats.value?.by_user || []) {
    map[user.user_id] = user.total
  }
  return map
})

// 管理员看到所有用户的数据，按提交人分组展示；顺序沿用列表的时间倒序。
const groupedItems = computed(() => groupGapsByUser(items.value))

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
      // el-select 清空后会给出空字符串，不能直接传给后端整数参数。
      user_id: filters.userId || undefined,
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
  filters.userId = null
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
          <span>共 {{ trendTotal }} 条</span>
        </div>
        <div class="gap-trend__plot">
          <div class="gap-trend__axis" aria-hidden="true">
            <span>{{ trendMax }}</span>
            <span>{{ trendHalf }}</span>
            <span>0</span>
          </div>
          <div class="gap-trend__bars">
            <div
              v-for="item in trendDays"
              :key="item.key"
              class="gap-trend__bar"
              :class="{ 'is-empty': !item.count }"
              :title="`${item.key}：${item.count} 条`"
            >
              <i
                :style="{
                  height: `${item.count ? Math.max(6, (item.count / trendMax) * 100) : 2}%`,
                }"
              />
              <small>{{ item.key.slice(5) }}</small>
            </div>
          </div>
        </div>
      </section>

      <section class="gap-filters">
        <el-select
          v-model="filters.userId"
          clearable
          placeholder="全部用户"
          aria-label="用户筛选"
          @change="applyFilters"
        >
          <el-option
            v-for="option in userOptions"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
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
        <el-button class="gap-filters__submit" @click="applyFilters">筛选</el-button>
        <el-button text @click="resetFilters">重置</el-button>
      </section>

      <section v-loading="loading" class="gap-list">
        <div v-if="!items.length && !loading" class="gap-empty">
          <strong>当前筛选下没有知识缺口</strong>
          <span>调整筛选条件，或等待新的未命中问题沉淀</span>
        </div>

        <section
          v-for="group in groupedItems"
          :key="group.key"
          class="gap-group"
        >
          <header class="gap-group__head">
            <strong>{{ group.username }}</strong>
            <span>
              本页 {{ group.items.length }} 条<template v-if="userTotalMap[group.userId]">
                · 共 {{ userTotalMap[group.userId] }} 条</template>
            </span>
          </header>

          <article v-for="item in group.items" :key="item.id" class="gap-card">
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
              <span title="混合检索归一化分：与问题无关的内容通常也有 0.65～0.75，不能单独用来判断相关性">
                向量 {{ formatScore(item.vector_score) }}
              </span>
              <span title="交叉编码器相关性概率：达到 0.25 才会进入回答，接近 0 表示资料答非所问">
                重排 {{ formatScore(item.rerank_score) }}
              </span>
              <span>模式 {{ item.retrieval_mode || '—' }}</span>
              <span>知识库 {{ (item.kb_ids || []).join(' / ') || '—' }}</span>
            </div>
            <p v-if="item.retrieval_mode === 'rerank'" class="gap-card__score-hint">
              向量分与重排分不是同一量纲，判断资料是否相关请以重排分为准。
            </p>

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

/* 通栏指标带，和知识库 / 评估页保持同一套版式 */
.gap-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border-top: 1px solid var(--atf-line-strong);
  border-bottom: 1px solid var(--atf-line-strong);
}

.gap-stat {
  display: flex;
  flex-direction: column;
  padding: 18px 26px 16px;
  background: transparent;
  border: 0;
  border-radius: 0;
  gap: 8px;
}

.gap-stat + .gap-stat {
  border-left: 1px solid var(--atf-line);
}

.gap-stat:first-child {
  padding-left: 0;
}

.gap-stat span {
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 9.5px;
  font-weight: 500;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.gap-stat strong {
  color: var(--atf-ink);
  font-family: var(--atf-font-display);
  font-size: clamp(32px, 3.2vw, 44px);
  font-weight: 500;
  letter-spacing: -0.02em;
  line-height: 1.05;
  font-variant-numeric: tabular-nums;
}

.gap-stat--orange strong,
.gap-stat--mint strong,
.gap-stat--gray strong {
  color: var(--atf-ink);
}

.gap-stat--violet strong {
  color: var(--atf-ink);
}

.gap-trend {
  padding: 16px 18px;
  margin-top: 14px;
  background: var(--atf-surface-raised);
  border: 1px solid var(--atf-line);
  border-radius: var(--atf-radius-md);
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

.gap-trend__plot {
  display: flex;
  align-items: flex-start;
  margin-top: 12px;
  gap: 10px;
}

.gap-trend__axis {
  display: flex;
  flex: none;
  flex-direction: column;
  justify-content: space-between;
  width: 22px;
  height: 90px;
  margin-bottom: 18px;
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 9px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.gap-trend__bars {
  display: flex;
  flex: 1;
  align-items: flex-end;
  height: 90px;
  border-bottom: 1px solid var(--atf-line-strong);
  background-image: repeating-linear-gradient(
    to top,
    var(--atf-line) 0 1px,
    transparent 1px 25%
  );
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
  background: var(--atf-brand);
  border-radius: 1px 1px 0 0;
  opacity: 0.85;
}

.gap-trend__bar.is-empty i {
  background: var(--atf-line-strong);
  opacity: 0.6;
}

.gap-trend__bar small {
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 9.5px;
  white-space: nowrap;
}

.gap-trend__bar.is-empty small {
  color: var(--atf-placeholder);
}

/* 筛选条做成两条细线之间的一条工具行，不再套卡片 */
.gap-filters {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  padding: 12px 0;
  margin: 18px 0;
  background: transparent;
  border: 0;
  border-top: 1px solid var(--atf-line-strong);
  border-bottom: 1px solid var(--atf-line);
  border-radius: 0;
  gap: 10px;
}

.gap-filters :deep(.el-select) {
  width: 140px;
}

.gap-filters :deep(.el-input) {
  max-width: 260px;
}

.gap-filters__submit {
  height: 32px;
  padding: 0 16px;
  color: var(--atf-brand-deep);
  font-size: 11.5px;
  background: transparent;
  border: 1px solid var(--atf-line-strong);
  border-radius: 0;
}

.gap-filters__submit:hover,
.gap-filters__submit:focus {
  color: var(--atf-brand);
  background: var(--atf-wash);
  border-color: var(--atf-brand);
}

.gap-list {
  display: flex;
  flex-direction: column;
  min-height: 160px;
  gap: 14px;
}

.gap-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.gap-group__head {
  display: flex;
  align-items: baseline;
  padding: 0 4px;
  color: var(--atf-ink-soft);
  gap: 8px;
}

.gap-group__head strong {
  font-size: 12px;
  font-weight: 720;
}

.gap-group__head span {
  color: var(--atf-muted);
  font-size: 10px;
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

.gap-empty strong {
  color: var(--atf-ink-soft);
  font-size: 13px;
}

.gap-empty span {
  font-size: 11px;
}

.gap-card {
  padding: 15px 17px;
  background: var(--atf-surface-raised);
  border: 1px solid var(--atf-line);
  border-radius: var(--atf-radius-md);
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
  font-family: var(--atf-font-mono);
  font-size: 9px;
  letter-spacing: 0.06em;
  border-radius: 1px;
}

.tone-violet { color: var(--atf-brand-deep); background: var(--atf-brand-soft); }
.tone-blue { color: var(--atf-tone-blue); background: var(--atf-tone-blue-bg); }
.tone-mint { color: var(--atf-mint); background: var(--atf-tone-mint-bg); }
.tone-orange { color: var(--atf-peach); background: var(--atf-tone-peach-bg); }
.tone-red { color: var(--atf-tone-red); background: var(--atf-tone-red-bg); }
.tone-pink { color: var(--atf-tone-pink); background: var(--atf-tone-pink-bg); }
.tone-gold { color: var(--atf-tone-gold); background: var(--atf-tone-gold-bg); }
.tone-teal { color: var(--atf-tone-teal); background: var(--atf-tone-teal-bg); }
.tone-gray { color: var(--atf-ink-soft); background: var(--atf-tone-gray-bg); }

.gap-card__source {
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 9.5px;
  font-variant-numeric: tabular-nums;
}

.gap-card__time {
  flex: none;
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
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
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  gap: 12px;
}

.gap-card__score-hint {
  margin-top: 5px;
  color: var(--atf-muted);
  font-size: 9.5px;
  line-height: 1.6;
}

.gap-card__note {
  padding: 8px 11px;
  margin-top: 9px;
  color: var(--atf-ink-soft);
  font-size: 11px;
  line-height: 1.65;
  background: var(--atf-surface);
  border-left: 2px solid var(--atf-line-strong);
  border-radius: 0;
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

  .gap-stat {
    padding: 16px 16px 14px;
  }

  .gap-stat:nth-child(odd) {
    padding-left: 0;
    border-left: 0;
  }

  .gap-stat:nth-child(n + 3) {
    border-top: 1px solid var(--atf-line);
  }

  .gap-filters {
    flex-wrap: wrap;
  }
}
</style>
