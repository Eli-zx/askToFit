<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Delete,
  Document,
  Refresh,
  TrendCharts,
  View,
} from '@element-plus/icons-vue'

import {
  deleteEvaluationRun,
  fetchEvaluationOverview,
  fetchEvaluationTrends,
  getEvaluationRun,
  listEvaluationCases,
  listEvaluationRuns,
} from '@/api/evaluation'
import { toErrorMessage } from '@/utils/errors'
import { formatDateTime, truncate } from '@/utils/format'
import {
  RUN_STATUS_META,
  deltaTone,
  formatDelta,
  formatDuration,
  formatMetric,
  formatPercent,
  metricLabel,
  strategyLabel,
} from '@/utils/evaluation'

const PAGE_SIZE = 10
const CASE_SIZE = 20
const TREND_LIMIT = 14

const RUN_COMMAND =
  'uv run python scripts/evaluate_rag.py --retrieval-only --limit 10'
const RAGAS_COMMAND =
  'uv run --with-requirements scripts/eval_requirements.txt \\\n' +
  '  python scripts/evaluate_rag.py --limit 5 --judge-metrics faithfulness,answer_relevancy'

const STRATEGIES = ['auto', 'direct', 'hyde', 'subquery', 'backtrack']
const METRIC_NAMES = ['hit_at_k', 'recall_at_k', 'mrr', 'ndcg_at_k']

const loading = ref(false)
const statsLoading = ref(false)
const trending = ref(false)
const overview = ref(null)
const trends = ref([])
const runs = ref([])
const runTotal = ref(0)
const page = ref(1)
const filters = reactive({ strategy: '', status: '' })

const detailVisible = ref(false)
const detailLoading = ref(false)
const activeRun = ref(null)
const cases = ref([])
const caseTotal = ref(0)
const casePage = ref(1)
const failedOnly = ref(false)
const casesLoading = ref(false)
const commandDialog = ref(false)

const strategyOptions = STRATEGIES.map((value) => ({
  value,
  label: strategyLabel(value),
}))

const statusOptions = Object.entries(RUN_STATUS_META).map(([value, meta]) => ({
  value,
  label: meta.label,
}))

function metricValue(run, level, name) {
  return run?.retrieval_metrics?.[level]?.[name] ?? null
}

function generationValue(run, name) {
  return run?.generation_metrics?.[name] ?? null
}

function latestGenerationName(run) {
  const metrics = run?.generation_metrics || {}
  const preferred = ['faithfulness', 'answer_relevancy']
  return preferred.find((name) => metrics[name] !== undefined && metrics[name] !== null)
}

const statsCards = computed(() => {
  const run = overview.value?.latest
  const delta = overview.value?.delta
  const genName = latestGenerationName(run)
  return [
    {
      label: '最近运行',
      value: run ? RUN_STATUS_META[run.status]?.label || run.status : '暂无',
      hint: run ? formatDateTime(run.finished_at || run.created_at) : '尚未写入评估记录',
      tone: 'violet',
      delta: null,
    },
    {
      label: 'Doc Hit@K',
      value: formatPercent(metricValue(run, 'doc', 'hit_at_k')),
      hint: `TopK ${run?.top_k ?? '—'} · ${run ? strategyLabel(run.strategy) : '—'}`,
      tone: 'mint',
      delta: delta?.retrieval?.doc?.hit_at_k ?? null,
    },
    {
      label: 'Section Recall@K',
      value: formatPercent(metricValue(run, 'section', 'recall_at_k')),
      hint: run ? `${run.sample_count} 条样本` : '暂无样本',
      tone: 'blue',
      delta: delta?.retrieval?.section?.recall_at_k ?? null,
    },
    {
      label: genName ? metricLabel(genName) : 'Faithfulness',
      value: formatPercent(genName ? generationValue(run, genName) : null),
      hint: run?.ragas_version ? `RAGAS ${run.ragas_version}` : '生成指标未运行',
      tone: 'orange',
      delta: genName ? (delta?.generation?.[genName] ?? null) : null,
    },
  ]
})

const trendPoints = computed(() => trends.value.slice(-TREND_LIMIT))

const trendMax = computed(() =>
  Math.max(
    0.0001,
    ...trendPoints.value.flatMap((item) => [
      Number(item.doc_hit_at_k) || 0,
      Number(item.section_recall_at_k) || 0,
    ]),
  ),
)

const pageCount = computed(() => Math.max(1, Math.ceil(runTotal.value / PAGE_SIZE)))
const casePageCount = computed(() => Math.max(1, Math.ceil(caseTotal.value / CASE_SIZE)))

const detailTitle = computed(() =>
  activeRun.value
    ? `评估运行 #${activeRun.value.id} · ${formatDateTime(activeRun.value.created_at)}`
    : '评估运行详情',
)

const activeDelta = computed(() => {
  if (!activeRun.value || overview.value?.latest?.id !== activeRun.value.id) return null
  return overview.value?.delta || null
})

function trendLabel(item) {
  return String(item.created_at || '').slice(5, 10)
}

function barHeight(value) {
  const number = Number(value)
  if (!Number.isFinite(number) || number <= 0) return '0%'
  return `${Math.max(3, (number / trendMax.value) * 100)}%`
}

function runTone(status) {
  return RUN_STATUS_META[status]?.type || 'info'
}

function hasDelta(value) {
  return value !== null && value !== undefined
}

function caseMetric(row, level, name) {
  return row?.[`${level}_metrics`]?.[name] ?? null
}

function caseGeneration(row) {
  return Object.entries(row?.generation_metrics || {}).filter(
    ([, value]) => value !== null && value !== undefined,
  )
}

function retrievedText(row) {
  const items = row?.retrieved || []
  if (!items.length) return '无命中'
  return items
    .slice(0, 5)
    .map((hit) => `${hit.doc_name || '未知文档'}${hit.section ? ` / ${hit.section}` : ''}`)
    .join('；')
}

async function loadOverview() {
  statsLoading.value = true
  try {
    overview.value = await fetchEvaluationOverview()
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '评估概览加载失败'))
  } finally {
    statsLoading.value = false
  }
}

async function loadTrends() {
  trending.value = true
  try {
    const payload = await fetchEvaluationTrends(TREND_LIMIT)
    trends.value = payload.items || []
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '评估趋势加载失败'))
  } finally {
    trending.value = false
  }
}

async function loadRuns() {
  loading.value = true
  try {
    const payload = await listEvaluationRuns({
      limit: PAGE_SIZE,
      offset: (page.value - 1) * PAGE_SIZE,
      strategy: filters.strategy || undefined,
      status: filters.status || undefined,
    })
    runs.value = payload.items || []
    runTotal.value = payload.total || 0
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '评估运行加载失败'))
  } finally {
    loading.value = false
  }
}

async function refresh() {
  await Promise.all([loadOverview(), loadTrends(), loadRuns()])
}

function applyFilters() {
  page.value = 1
  loadRuns()
}

function resetFilters() {
  filters.strategy = ''
  filters.status = ''
  applyFilters()
}

function previousPage() {
  if (page.value <= 1) return
  page.value -= 1
  loadRuns()
}

function nextPage() {
  if (page.value >= pageCount.value) return
  page.value += 1
  loadRuns()
}

async function loadActiveRun(runId) {
  detailLoading.value = true
  try {
    activeRun.value = await getEvaluationRun(runId)
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '运行详情加载失败'))
  } finally {
    detailLoading.value = false
  }
}

async function loadCases() {
  if (!activeRun.value) return
  casesLoading.value = true
  try {
    const payload = await listEvaluationCases(activeRun.value.id, {
      limit: CASE_SIZE,
      offset: (casePage.value - 1) * CASE_SIZE,
      failed_only: failedOnly.value,
    })
    cases.value = payload.items || []
    caseTotal.value = payload.total || 0
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '逐题结果加载失败'))
  } finally {
    casesLoading.value = false
  }
}

async function openRun(run) {
  detailVisible.value = true
  activeRun.value = run
  cases.value = []
  caseTotal.value = 0
  casePage.value = 1
  failedOnly.value = false
  await Promise.all([loadActiveRun(run.id), loadCases()])
}

function toggleFailedOnly() {
  casePage.value = 1
  loadCases()
}

function previousCasePage() {
  if (casePage.value <= 1) return
  casePage.value -= 1
  loadCases()
}

function nextCasePage() {
  if (casePage.value >= casePageCount.value) return
  casePage.value += 1
  loadCases()
}

async function removeRun(run) {
  try {
    await ElMessageBox.confirm(
      `确定删除运行 #${run.id}（${formatDateTime(run.created_at)}）？逐题明细会一并删除，且不可恢复。`,
      '删除评估运行',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }
  try {
    await deleteEvaluationRun(run.id)
    ElMessage.success('评估运行已删除')
    if (activeRun.value?.id === run.id) {
      detailVisible.value = false
      activeRun.value = null
    }
    await refresh()
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '删除评估运行失败'))
  }
}

async function copyCommand(command) {
  try {
    await navigator.clipboard.writeText(command)
    ElMessage.success('命令已复制')
  } catch {
    ElMessage.warning('当前浏览器不允许自动复制，请手动选择命令')
  }
}

onMounted(refresh)
</script>

<template>
  <div class="atf-page">
    <div class="atf-page-inner">
      <header class="eval-head">
        <div>
          <span class="atf-eyebrow">Retrieval quality lab</span>
          <h1 class="atf-title">
            评估<span class="atf-gradient-text">看板</span>
          </h1>
          <p class="atf-subtitle">
            检索指标由本地口径计算，生成指标来自官方 RAGAS。评估仍由 CLI 运行，看板只读展示结果。
          </p>
        </div>
        <div class="eval-head__actions">
          <el-button @click="commandDialog = true">如何运行评估</el-button>
          <el-button :loading="loading || statsLoading || trending" @click="refresh">
            <el-icon><Refresh /></el-icon>
            刷新
          </el-button>
        </div>
      </header>

      <section class="eval-stats">
        <article
          v-for="card in statsCards"
          :key="card.label"
          class="eval-stat"
          :class="`eval-stat--${card.tone}`"
        >
          <span class="eval-stat__label">{{ card.label }}</span>
          <strong class="eval-stat__value">{{ card.value }}</strong>
          <span class="eval-stat__hint">
            {{ card.hint }}
            <em v-if="hasDelta(card.delta)" :class="`delta-${deltaTone(card.delta)}`">
              {{ formatDelta(card.delta) }}
            </em>
          </span>
        </article>
        <el-skeleton v-if="statsLoading && !overview" :rows="1" animated />
      </section>

      <section class="eval-trend">
        <div class="eval-trend__head">
          <strong>最近 {{ trendPoints.length || TREND_LIMIT }} 次运行</strong>
          <span class="eval-trend__legend">
            <i class="dot-doc" /> Doc Hit@K
            <i class="dot-section" /> Section Recall@K
          </span>
        </div>
        <div v-if="trendPoints.length" class="eval-trend__bars">
          <div
            v-for="point in trendPoints"
            :key="point.id"
            class="eval-trend__group"
            :title="`#${point.id} ${formatDateTime(point.created_at)} · Doc Hit ${formatPercent(point.doc_hit_at_k)} · Section Recall ${formatPercent(point.section_recall_at_k)}`"
          >
            <div class="eval-trend__bars-inner">
              <i class="bar-doc" :style="{ height: barHeight(point.doc_hit_at_k) }" />
              <i class="bar-section" :style="{ height: barHeight(point.section_recall_at_k) }" />
            </div>
            <small>{{ trendLabel(point) }}</small>
          </div>
        </div>
        <div v-else class="eval-trend__empty">
          {{ trending ? '趋势加载中…' : '暂无趋势数据，运行一次评估后展示' }}
        </div>
      </section>

      <section class="eval-filters">
        <el-select
          v-model="filters.strategy"
          clearable
          placeholder="全部策略"
          aria-label="策略筛选"
          @change="applyFilters"
        >
          <el-option
            v-for="option in strategyOptions"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-select
          v-model="filters.status"
          clearable
          placeholder="全部状态"
          aria-label="状态筛选"
          @change="applyFilters"
        >
          <el-option
            v-for="option in statusOptions"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-button type="primary" @click="applyFilters">筛选</el-button>
        <el-button text @click="resetFilters">重置</el-button>
        <span class="eval-filters__total">共 {{ runTotal }} 次运行</span>
      </section>

      <section v-loading="loading" class="eval-table">
        <el-table :data="runs" row-key="id" size="small" @row-dblclick="openRun">
          <el-table-column label="运行时间" min-width="150">
            <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="dataset_name" label="数据集" min-width="150" show-overflow-tooltip />
          <el-table-column label="策略" width="110">
            <template #default="{ row }">{{ strategyLabel(row.strategy) }}</template>
          </el-table-column>
          <el-table-column prop="top_k" label="TopK" width="70" align="center" />
          <el-table-column label="Doc Hit@K" width="100" align="right">
            <template #default="{ row }">
              {{ formatPercent(metricValue(row, 'doc', 'hit_at_k')) }}
            </template>
          </el-table-column>
          <el-table-column label="Section Recall@K" width="130" align="right">
            <template #default="{ row }">
              {{ formatPercent(metricValue(row, 'section', 'recall_at_k')) }}
            </template>
          </el-table-column>
          <el-table-column label="生成指标" width="110">
            <template #default="{ row }">
              <span v-if="row.ragas_version">{{ row.ragas_version }}</span>
              <span v-else class="eval-muted">未运行</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="95">
            <template #default="{ row }">
              <el-tag :type="runTone(row.status)" size="small" effect="light">
                {{ RUN_STATUS_META[row.status]?.label || row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="140" align="right">
            <template #default="{ row }">
              <el-button size="small" text type="primary" @click="openRun(row)">
                <el-icon><View /></el-icon>
                详情
              </el-button>
              <el-button size="small" text type="danger" @click="removeRun(row)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </template>
          </el-table-column>
          <template #empty>
            <div class="eval-empty">
              <div class="eval-empty__icon">
                <el-icon><TrendCharts /></el-icon>
              </div>
              <strong>还没有评估运行</strong>
              <span>按下方命令执行一次评估，结果会写入这里</span>
              <el-button size="small" type="primary" plain @click="commandDialog = true">
                查看命令
              </el-button>
            </div>
          </template>
        </el-table>

        <div class="eval-pager">
          <span>第 {{ page }} / {{ pageCount }} 页</span>
          <el-button size="small" :disabled="page <= 1" @click="previousPage">上一页</el-button>
          <el-button size="small" :disabled="page >= pageCount" @click="nextPage">下一页</el-button>
        </div>
      </section>
    </div>

    <el-drawer
      v-model="detailVisible"
      :title="detailTitle"
      size="76%"
      append-to-body
      class="eval-drawer"
    >
      <div v-loading="detailLoading" class="eval-detail">
        <template v-if="activeRun">
          <section class="eval-detail__meta">
            <div>
              <span>数据集</span>
              <strong>{{ activeRun.dataset_name }}</strong>
            </div>
            <div>
              <span>知识库</span>
              <strong>{{ activeRun.kb_name || `#${activeRun.kb_id ?? '—'}` }}</strong>
            </div>
            <div>
              <span>策略 / TopK</span>
              <strong>{{ strategyLabel(activeRun.strategy) }} / {{ activeRun.top_k }}</strong>
            </div>
            <div>
              <span>样本</span>
              <strong>{{ activeRun.answerable_count }} / {{ activeRun.sample_count }} 可回答</strong>
            </div>
            <div>
              <span>judge 模型</span>
              <strong>{{ activeRun.judge_model || '—' }}</strong>
            </div>
            <div>
              <span>耗时</span>
              <strong>{{ formatDuration(activeRun.started_at, activeRun.finished_at) }}</strong>
            </div>
          </section>

          <section class="eval-detail__metrics">
            <div v-for="level in ['doc', 'section']" :key="level" class="eval-metric-block">
              <h3>{{ level === 'doc' ? '文档级检索' : '章节级检索' }}</h3>
              <div class="eval-metric-grid">
                <div v-for="name in METRIC_NAMES" :key="name" class="eval-metric">
                  <span>{{ metricLabel(name) }}</span>
                  <strong>{{ formatMetric(metricValue(activeRun, level, name)) }}</strong>
                  <em
                    v-if="hasDelta(activeDelta?.retrieval?.[level]?.[name])"
                    :class="`delta-${deltaTone(activeDelta?.retrieval?.[level]?.[name])}`"
                  >
                    {{ formatDelta(activeDelta?.retrieval?.[level]?.[name]) }}
                  </em>
                </div>
              </div>
            </div>
            <div class="eval-metric-block">
              <h3>生成质量（官方 RAGAS）</h3>
              <div v-if="Object.keys(activeRun.generation_metrics || {}).length" class="eval-metric-grid">
                <div
                  v-for="(value, name) in activeRun.generation_metrics"
                  :key="name"
                  class="eval-metric"
                >
                  <span>{{ metricLabel(name) }}</span>
                  <strong>{{ formatPercent(value) }}</strong>
                  <em
                    v-if="hasDelta(activeDelta?.generation?.[name])"
                    :class="`delta-${deltaTone(activeDelta?.generation?.[name])}`"
                  >
                    {{ formatDelta(activeDelta?.generation?.[name]) }}
                  </em>
                </div>
              </div>
              <p v-else class="eval-muted">
                本次运行没有生成指标（retrieval-only 或 RAGAS 未安装）。该状态不计 0 分。
              </p>
            </div>
          </section>

          <section class="eval-cases">
            <div class="eval-cases__head">
              <div>
                <strong>逐题结果</strong>
                <span>{{ caseTotal }} 条{{ failedOnly ? '失败记录' : '记录' }}</span>
              </div>
              <div class="eval-cases__tools">
                <el-switch
                  v-model="failedOnly"
                  active-text="仅看失败"
                  size="small"
                  @change="toggleFailedOnly"
                />
                <el-button size="small" :loading="casesLoading" @click="loadCases">
                  刷新
                </el-button>
              </div>
            </div>

            <el-table
              v-loading="casesLoading"
              :data="cases"
              row-key="id"
              size="small"
              max-height="460"
            >
              <el-table-column type="expand">
                <template #default="{ row }">
                  <div class="eval-case-expand">
                    <div class="eval-case-expand__section">
                      <span>问题</span>
                      <p>{{ row.question }}</p>
                    </div>
                    <div class="eval-case-expand__section">
                      <span>参考答案</span>
                      <p>{{ row.ground_truth || '—' }}</p>
                    </div>
                    <div class="eval-case-expand__section">
                      <span>模型回答</span>
                      <p>{{ row.answer || '—' }}</p>
                    </div>
                    <div class="eval-case-expand__section">
                      <span>期望证据</span>
                      <p>
                        文档：{{ (row.expected_doc_names || []).join('、') || '—' }}
                        <br />
                        章节：{{ (row.expected_sections || []).join('、') || '—' }}
                      </p>
                    </div>
                    <div class="eval-case-expand__section">
                      <span>命中摘要</span>
                      <p>{{ retrievedText(row) }}</p>
                    </div>
                    <div v-if="caseGeneration(row).length" class="eval-case-expand__chips">
                      <span
                        v-for="[name, value] in caseGeneration(row)"
                        :key="name"
                        class="eval-chip"
                      >
                        {{ metricLabel(name) }} {{ formatPercent(value) }}
                      </span>
                    </div>
                    <p v-if="row.error" class="eval-case-expand__error">{{ row.error }}</p>
                  </div>
                </template>
              </el-table-column>
              <el-table-column prop="case_id" label="编号" width="90" />
              <el-table-column label="问题" min-width="260" show-overflow-tooltip>
                <template #default="{ row }">{{ truncate(row.question, 70) }}</template>
              </el-table-column>
              <el-table-column label="意图" width="110">
                <template #default="{ row }">{{ row.intent || '—' }}</template>
              </el-table-column>
              <el-table-column label="Doc Hit" width="85" align="right">
                <template #default="{ row }">
                  {{ formatMetric(caseMetric(row, 'doc', 'hit_at_k'), 0) }}
                </template>
              </el-table-column>
              <el-table-column label="Section Hit" width="105" align="right">
                <template #default="{ row }">
                  {{ formatMetric(caseMetric(row, 'section', 'hit_at_k'), 0) }}
                </template>
              </el-table-column>
              <el-table-column label="关键词" width="80" align="right">
                <template #default="{ row }">
                  {{ formatPercent(row.keyword_coverage) }}
                </template>
              </el-table-column>
              <el-table-column label="Faithfulness" width="110" align="right">
                <template #default="{ row }">
                  {{ formatPercent(row.generation_metrics?.faithfulness) }}
                </template>
              </el-table-column>
              <el-table-column label="状态" width="80">
                <template #default="{ row }">
                  <el-tag v-if="row.error" type="danger" size="small" effect="light">失败</el-tag>
                  <el-tag v-else type="success" size="small" effect="light">完成</el-tag>
                </template>
              </el-table-column>
              <template #empty>
                <div class="eval-empty eval-empty--small">
                  <el-icon><Document /></el-icon>
                  <span>{{ failedOnly ? '没有失败记录' : '没有逐题数据' }}</span>
                </div>
              </template>
            </el-table>

            <div class="eval-pager">
              <span>第 {{ casePage }} / {{ casePageCount }} 页</span>
              <el-button size="small" :disabled="casePage <= 1" @click="previousCasePage">
                上一页
              </el-button>
              <el-button
                size="small"
                :disabled="casePage >= casePageCount"
                @click="nextCasePage"
              >
                下一页
              </el-button>
            </div>
          </section>
        </template>
      </div>
    </el-drawer>

    <el-dialog v-model="commandDialog" title="如何运行评估" width="720px" append-to-body>
      <div class="eval-command">
        <p>先跑检索指标，不需要 RAGAS 与 API Key：</p>
        <div class="eval-command__row">
          <pre>{{ RUN_COMMAND }}</pre>
          <el-button size="small" @click="copyCommand(RUN_COMMAND)">复制</el-button>
        </div>
        <p>再跑官方 RAGAS 生成指标（依赖装在独立 overlay，不进入运行时环境）：</p>
        <div class="eval-command__row">
          <pre>{{ RAGAS_COMMAND }}</pre>
          <el-button size="small" @click="copyCommand(RAGAS_COMMAND)">复制</el-button>
        </div>
        <p class="eval-muted">
          评估结果默认写入 eval_runs / eval_cases；命令不会输出任何 API Key。
          运行前请确认 MySQL、Milvus 与本地 BGE 模型可用。
        </p>
      </div>
      <template #footer>
        <el-button type="primary" @click="commandDialog = false">知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.eval-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 24px;
  gap: 18px;
}

.eval-head__actions {
  display: flex;
  flex: none;
  gap: 8px;
}

.eval-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.eval-stat {
  display: flex;
  flex-direction: column;
  padding: 16px 18px;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 15px;
  box-shadow: var(--atf-shadow-sm);
  gap: 8px;
}

.eval-stat__label {
  color: var(--atf-muted);
  font-size: 11px;
  font-weight: 650;
}

.eval-stat__value {
  color: var(--atf-ink);
  font-size: 26px;
  font-weight: 760;
  font-variant-numeric: tabular-nums;
}

.eval-stat__hint {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  color: #9a9db2;
  font-size: 10px;
  gap: 6px;
}

.eval-stat--violet strong { color: var(--atf-brand-deep); }
.eval-stat--mint strong { color: #1d8a70; }
.eval-stat--blue strong { color: #2563a8; }
.eval-stat--orange strong { color: #b06a1c; }

.delta-up { color: #1d8a70; font-style: normal; font-weight: 700; }
.delta-down { color: #b03a47; font-style: normal; font-weight: 700; }
.delta-flat { color: #9a9db2; font-style: normal; }

.eval-trend {
  padding: 16px 18px;
  margin-top: 14px;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 15px;
}

.eval-trend__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--atf-ink-soft);
  font-size: 12px;
}

.eval-trend__legend {
  display: inline-flex;
  align-items: center;
  color: var(--atf-muted);
  font-size: 10px;
  gap: 5px;
}

.eval-trend__legend i {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-left: 7px;
  border-radius: 2px;
}

.dot-doc { background: #5b5bd6; }
.dot-section { background: #35b795; }

.eval-trend__bars {
  display: flex;
  align-items: flex-end;
  height: 110px;
  margin-top: 14px;
  gap: 8px;
}

.eval-trend__group {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  height: 100%;
  gap: 6px;
}

.eval-trend__bars-inner {
  display: flex;
  align-items: flex-end;
  justify-content: center;
  width: 100%;
  height: 100%;
  gap: 3px;
}

.eval-trend__bars-inner i {
  width: 42%;
  max-width: 14px;
  border-radius: 4px 4px 2px 2px;
  opacity: 0.86;
  transition: height 0.3s ease;
}

.bar-doc { background: linear-gradient(180deg, #8b7cf6, #5b5bd6); }
.bar-section { background: linear-gradient(180deg, #6fe0c4, #2f9e80); }

.eval-trend__group small {
  color: #aeb1c5;
  font-size: 8.5px;
  white-space: nowrap;
}

.eval-trend__empty {
  padding: 30px 0 16px;
  color: var(--atf-muted);
  font-size: 11px;
  text-align: center;
}

.eval-filters {
  display: flex;
  align-items: center;
  padding: 12px;
  margin: 16px 0;
  background: #fbfaff;
  border: 1px solid var(--atf-line);
  border-radius: 13px;
  gap: 10px;
}

.eval-filters :deep(.el-select) {
  width: 150px;
}

.eval-filters__total {
  margin-left: auto;
  color: var(--atf-muted);
  font-size: 11px;
}

.eval-table {
  min-height: 180px;
}

.eval-pager {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  margin-top: 14px;
  color: var(--atf-muted);
  font-size: 11px;
  gap: 8px;
}

.eval-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 46px 20px;
  color: var(--atf-muted);
  text-align: center;
  gap: 6px;
}

.eval-empty--small {
  padding: 22px 12px;
  font-size: 11px;
}

.eval-empty__icon {
  display: grid;
  width: 44px;
  height: 44px;
  margin-bottom: 4px;
  color: var(--atf-brand);
  font-size: 20px;
  background: var(--atf-brand-soft);
  border-radius: 50%;
  place-items: center;
}

.eval-empty strong {
  color: var(--atf-ink-soft);
  font-size: 13px;
}

.eval-empty span {
  font-size: 11px;
}

.eval-muted {
  color: #9a9db2;
  font-size: 11px;
}

.eval-detail__meta {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.eval-detail__meta > div {
  display: flex;
  flex-direction: column;
  padding: 10px 12px;
  background: #fbfaff;
  border: 1px solid var(--atf-line);
  border-radius: 11px;
  gap: 4px;
}

.eval-detail__meta span {
  color: var(--atf-muted);
  font-size: 9.5px;
}

.eval-detail__meta strong {
  color: var(--atf-ink-soft);
  font-size: 12px;
  font-weight: 650;
}

.eval-detail__metrics {
  display: flex;
  flex-direction: column;
  margin-top: 16px;
  gap: 12px;
}

.eval-metric-block h3 {
  margin-bottom: 8px;
  color: var(--atf-ink-soft);
  font-size: 12px;
  font-weight: 700;
}

.eval-metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.eval-metric {
  display: flex;
  flex-direction: column;
  padding: 10px 12px;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 11px;
  gap: 4px;
}

.eval-metric span {
  color: var(--atf-muted);
  font-size: 9.5px;
}

.eval-metric strong {
  color: var(--atf-ink);
  font-size: 18px;
  font-weight: 740;
  font-variant-numeric: tabular-nums;
}

.eval-metric em {
  font-size: 9.5px;
}

.eval-cases {
  margin-top: 18px;
}

.eval-cases__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 9px;
  gap: 12px;
}

.eval-cases__head strong {
  color: var(--atf-ink-soft);
  font-size: 12px;
}

.eval-cases__head span {
  margin-left: 8px;
  color: var(--atf-muted);
  font-size: 10px;
}

.eval-cases__tools {
  display: flex;
  align-items: center;
  gap: 10px;
}

.eval-case-expand {
  padding: 10px 16px 14px 46px;
  color: var(--atf-ink-soft);
  font-size: 11px;
}

.eval-case-expand__section {
  margin-bottom: 10px;
}

.eval-case-expand__section span {
  display: block;
  margin-bottom: 3px;
  color: var(--atf-muted);
  font-size: 9.5px;
  font-weight: 650;
}

.eval-case-expand__section p {
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.eval-case-expand__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.eval-chip {
  padding: 2px 8px;
  color: #5b4bc4;
  font-size: 9.5px;
  background: #eeebff;
  border-radius: 999px;
}

.eval-case-expand__error {
  padding: 7px 10px;
  margin-top: 8px;
  color: #b03a47;
  line-height: 1.6;
  background: #fff1f2;
  border-radius: 8px;
}

.eval-command p {
  margin-bottom: 8px;
  color: var(--atf-ink-soft);
  font-size: 12px;
}

.eval-command__row {
  display: flex;
  align-items: flex-start;
  margin-bottom: 14px;
  gap: 8px;
}

.eval-command__row pre {
  flex: 1;
  padding: 10px 12px;
  overflow-x: auto;
  color: #4f5180;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  line-height: 1.6;
  white-space: pre-wrap;
  background: #f6f5ff;
  border: 1px solid var(--atf-line);
  border-radius: 10px;
}

@media (max-width: 900px) {
  .eval-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .eval-detail__meta,
  .eval-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .eval-head {
    flex-direction: column;
  }
}
</style>
