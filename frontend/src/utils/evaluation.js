export const RUN_STATUS_META = {
  success: { label: '成功', type: 'success' },
  partial: { label: '部分失败', type: 'warning' },
  failed: { label: '失败', type: 'danger' },
}

export const METRIC_LABELS = {
  hit_at_k: 'Hit@K',
  recall_at_k: 'Recall@K',
  mrr: 'MRR',
  ndcg_at_k: 'NDCG@K',
  faithfulness: 'Faithfulness',
  answer_relevancy: 'Answer Relevancy',
  context_precision: 'Context Precision',
  context_recall: 'Context Recall',
  llm_context_precision_with_reference: 'Context Precision',
}

export const STRATEGY_LABELS = {
  auto: '自动规划',
  direct: '直接检索',
  hyde: 'HyDE',
  subquery: '子查询',
  backtrack: '回溯',
}

export function formatPercent(value, digits = 1) {
  const number = Number(value)
  if (value === null || value === undefined || !Number.isFinite(number)) return '—'
  return `${(number * 100).toFixed(digits)}%`
}

export function formatMetric(value, digits = 3) {
  const number = Number(value)
  if (value === null || value === undefined || !Number.isFinite(number)) return '—'
  return number.toFixed(digits)
}

export function formatDuration(startedAt, finishedAt) {
  if (!startedAt || !finishedAt) return '—'
  const start = new Date(startedAt).getTime()
  const end = new Date(finishedAt).getTime()
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return '—'
  const seconds = Math.round((end - start) / 1000)
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`
}

export function deltaTone(delta) {
  const number = Number(delta)
  if (delta === null || delta === undefined || !Number.isFinite(number)) return 'flat'
  if (number > 0) return 'up'
  if (number < 0) return 'down'
  return 'flat'
}

export function formatDelta(delta, digits = 1) {
  const number = Number(delta)
  if (delta === null || delta === undefined || !Number.isFinite(number)) return '—'
  const sign = number > 0 ? '+' : ''
  return `${sign}${(number * 100).toFixed(digits)}%`
}

export function metricLabel(name) {
  return METRIC_LABELS[name] || name
}

export function strategyLabel(strategy) {
  return STRATEGY_LABELS[strategy] || strategy || '—'
}
