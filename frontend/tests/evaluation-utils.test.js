import { describe, expect, it } from 'vitest'

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

describe('evaluation utils', () => {
  it('formats percentages and keeps unavailable values blank', () => {
    expect(formatPercent(0.8342)).toBe('83.4%')
    expect(formatPercent(1)).toBe('100.0%')
    expect(formatPercent(null)).toBe('—')
    expect(formatPercent(undefined)).toBe('—')
    expect(formatPercent(Number.NaN)).toBe('—')
  })

  it('formats raw metrics with fixed digits', () => {
    expect(formatMetric(0.12345)).toBe('0.123')
    expect(formatMetric(2, 2)).toBe('2.00')
    expect(formatMetric(null)).toBe('—')
  })

  it('formats durations by seconds and minutes', () => {
    expect(formatDuration('2026-10-18T10:00:00Z', '2026-10-18T10:00:42Z')).toBe('42 秒')
    expect(formatDuration('2026-10-18T10:00:00Z', '2026-10-18T10:03:05Z')).toBe('3 分 5 秒')
    expect(formatDuration(null, '2026-10-18T10:03:05Z')).toBe('—')
  })

  it('marks delta direction for styling', () => {
    expect(deltaTone(0.12)).toBe('up')
    expect(deltaTone(-0.2)).toBe('down')
    expect(deltaTone(0)).toBe('flat')
    expect(deltaTone(null)).toBe('flat')
    expect(formatDelta(0.031)).toBe('+3.1%')
    expect(formatDelta(-0.02)).toBe('-2.0%')
    expect(formatDelta(null)).toBe('—')
  })

  it('maps run status and metric labels', () => {
    expect(RUN_STATUS_META.success.label).toBe('成功')
    expect(RUN_STATUS_META.partial.type).toBe('warning')
    expect(metricLabel('faithfulness')).toBe('Faithfulness')
    expect(metricLabel('llm_context_precision_with_reference')).toBe('Context Precision')
    expect(metricLabel('custom_metric')).toBe('custom_metric')
    expect(strategyLabel('backtrack')).toBe('回溯')
    expect(strategyLabel('unknown')).toBe('unknown')
  })
})
