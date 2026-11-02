<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Delete,
  Document,
  Refresh,
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
  isMeaningfulRun,
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

const minSamples = computed(() => overview.value?.min_samples ?? 10)

const excludedRuns = computed(() => {
  if (!overview.value) return 0
  return Math.max(0, (overview.value.total_runs || 0) - (overview.value.meaningful_runs || 0))
})

const statsCards = computed(() => {
  const run = overview.value?.latest
  const delta = overview.value?.delta
  const genName = latestGenerationName(run)
  return [
    {
      label: '最近运行',
      value: run ? RUN_STATUS_META[run.status]?.label || run.status : '暂无',
      hint: run
        ? formatDateTime(run.finished_at || run.created_at)
        : excludedRuns.value
          ? `${excludedRuns.value} 次运行样本不足 ${minSamples.value} 条，未计入概览`
          : '尚未写入评估记录',
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
      hint: run ? `${run.sample_count} 条样本` : `需 ≥ ${minSamples.value} 条样本`,
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
  // 指标本身是 0–1 的比例，按 100% 固定刻度绘制，柱高才可跨图比较
  return `${Math.max(3, Math.min(1, number) * 100)}%`
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
            检索命中与回答忠实度按统一口径计算，每次运行都会沉淀，便于对比迭代效果。
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
          <strong>最近 {{ trendPoints.length || TREND_LIMIT }} 次有效运行</strong>
          <span class="eval-trend__legend">
            <i class="dot-doc" /> Doc Hit@K
            <i class="dot-section" /> Section Recall@K
            <span class="eval-trend__threshold">样本 ≥ {{ minSamples }} 条</span>
          </span>
        </div>
        <div v-if="trendPoints.length" class="eval-trend__plot">
          <div class="eval-trend__axis" aria-hidden="true">
            <span>100%</span>
            <span>50%</span>
            <span>0</span>
          </div>
          <div class="eval-trend__bars">
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
        <el-button class="eval-filters__submit" @click="applyFilters">筛选</el-button>
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
          <el-table-column label="样本" width="96" align="center">
            <template #default="{ row }">
              <div class="eval-samples">
                <span>{{ row.sample_count }}</span>
                <el-tag
                  v-if="!isMeaningfulRun(row)"
                  type="info"
                  size="small"
                  effect="plain"
                  class="eval-sample-tag"
                >
                  样本不足
                </el-tag>
              </div>
            </template>
          </el-table-column>
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
              <strong>
                {{ activeRun.answerable_count }} / {{ activeRun.sample_count }} 可回答
                <el-tag
                  v-if="!isMeaningfulRun(activeRun)"
                  type="info"
                  size="small"
                  effect="plain"
                  class="eval-sample-tag"
                >
                  样本不足，不计入概览
                </el-tag>
              </strong>
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
        <p class="eval-muted">
          概览与趋势只统计样本数 ≥ EVAL_MIN_MEANINGFUL_SAMPLES（默认 10）的运行；
          低于阈值的运行仍保留在列表中，并标注“样本不足”。
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

/* 与知识库页一致：通栏指标带，而不是四张卡片 */
.eval-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border-top: 1px solid var(--atf-line-strong);
  border-bottom: 1px solid var(--atf-line-strong);
}

.eval-stat {
  display: flex;
  flex-direction: column;
  padding: 18px 26px 16px;
  background: transparent;
  border: 0;
  border-radius: 0;
  gap: 8px;
}

.eval-stat + .eval-stat {
  border-left: 1px solid var(--atf-line);
}

.eval-stat:first-child {
  padding-left: 0;
}

.eval-stat__label {
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 9.5px;
  font-weight: 500;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.eval-stat__value {
  color: var(--atf-ink);
  font-family: var(--atf-font-display);
  font-size: clamp(32px, 3.2vw, 44px);
  font-weight: 500;
  letter-spacing: -0.02em;
  line-height: 1.05;
  font-variant-numeric: tabular-nums;
}

.eval-stat__hint {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  color: var(--atf-muted);
  font-size: 10px;
  gap: 6px;
}

/* 指标数值统一用墨色，状态色只留给真正需要警示的地方 */
.eval-stat--violet strong,
.eval-stat--mint strong,
.eval-stat--blue strong,
.eval-stat--orange strong {
  color: var(--atf-ink);
}

.delta-up { color: var(--atf-mint); font-style: normal; font-weight: 700; }
.delta-down { color: var(--atf-brand-deep); font-style: normal; font-weight: 700; }
.delta-flat { color: var(--atf-muted); font-style: normal; }

.eval-trend {
  padding: 16px 18px;
  margin-top: 14px;
  background: var(--atf-surface-raised);
  border: 1px solid var(--atf-line);
  border-radius: var(--atf-radius-md);
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
  width: 11px;
  height: 1px;
  margin-left: 7px;
  border-radius: 0;
}

.eval-trend__threshold {
  margin-left: 10px;
  padding-left: 10px;
  border-left: 1px solid var(--atf-line);
}

.eval-samples {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
}

.eval-sample-tag {
  margin-left: 4px;
}

.dot-doc { background: var(--atf-brand); }
.dot-section { background: var(--atf-bronze); }

.eval-trend__plot {
  display: flex;
  align-items: flex-start;
  margin-top: 14px;
  gap: 10px;
}

.eval-trend__axis {
  display: flex;
  flex: none;
  flex-direction: column;
  justify-content: space-between;
  width: 30px;
  height: 110px;
  margin-bottom: 18px;
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 9px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.eval-trend__bars {
  display: flex;
  flex: 1;
  align-items: flex-end;
  justify-content: center;
  height: 110px;
  border-bottom: 1px solid var(--atf-line-strong);
  background-image: repeating-linear-gradient(
    to top,
    var(--atf-line) 0 1px,
    transparent 1px 25%
  );
  gap: 8px;
}

.eval-trend__group {
  display: flex;
  flex: 1 1 54px;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  max-width: 88px;
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
  border-radius: 1px 1px 0 0;
  opacity: 0.88;
  transition: height 0.3s ease;
}

.bar-doc { background: var(--atf-brand); }
.bar-section { background: var(--atf-bronze); }

.eval-trend__group small {
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 9.5px;
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
  padding: 12px 0;
  margin: 18px 0;
  background: transparent;
  border: 0;
  border-top: 1px solid var(--atf-line-strong);
  border-bottom: 1px solid var(--atf-line);
  border-radius: 0;
  gap: 10px;
}

.eval-filters :deep(.el-select) {
  width: 150px;
}

.eval-filters__submit {
  height: 32px;
  padding: 0 16px;
  color: var(--atf-brand-deep);
  font-size: 11.5px;
  background: transparent;
  border: 1px solid var(--atf-line-strong);
  border-radius: 0;
}

.eval-filters__submit:hover,
.eval-filters__submit:focus {
  color: var(--atf-brand);
  background: var(--atf-wash);
  border-color: var(--atf-brand);
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

.eval-empty strong {
  color: var(--atf-ink-soft);
  font-size: 13px;
}

.eval-empty span {
  font-size: 11px;
}

.eval-muted {
  color: var(--atf-muted);
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
  background: var(--atf-surface);
  border: 1px solid var(--atf-line);
  border-radius: var(--atf-radius-sm);
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
  background: var(--atf-surface-raised);
  border: 1px solid var(--atf-line);
  border-radius: var(--atf-radius-sm);
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
  color: var(--atf-brand-deep);
  font-family: var(--atf-font-mono);
  font-size: 9px;
  background: var(--atf-brand-soft);
  border-radius: 1px;
}

.eval-case-expand__error {
  padding: 7px 10px;
  margin-top: 8px;
  color: var(--atf-brand-deep);
  line-height: 1.6;
  background: var(--atf-tone-pink-bg);
  border-left: 2px solid var(--atf-brand);
  border-radius: 0;
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
  color: var(--atf-ink-soft);
  font-family: var(--atf-font-mono);
  font-size: 11px;
  line-height: 1.6;
  white-space: pre-wrap;
  background: var(--atf-surface);
  border: 1px solid var(--atf-line);
  border-radius: var(--atf-radius-sm);
}

@media (max-width: 900px) {
  .eval-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .eval-stat {
    padding: 16px 16px 14px;
  }

  .eval-stat:nth-child(odd) {
    padding-left: 0;
    border-left: 0;
  }

  .eval-stat:nth-child(n + 3) {
    border-top: 1px solid var(--atf-line);
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
