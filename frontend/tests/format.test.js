import { describe, expect, it } from 'vitest'

import {
  formatDateTime,
  formatFileSize,
  formatLatency,
  formatRelativeTime,
  formatScore,
  formatTime,
  truncate,
} from '@/utils/format'

describe('formatFileSize', () => {
  it('格式化常见文件大小', () => {
    expect(formatFileSize(0)).toBe('0 B')
    expect(formatFileSize(-1)).toBe('0 B')
    expect(formatFileSize('')).toBe('0 B')
    expect(formatFileSize(512)).toBe('512 B')
    expect(formatFileSize(1024)).toBe('1.0 KB')
    expect(formatFileSize(1536)).toBe('1.5 KB')
    expect(formatFileSize(5 * 1024 * 1024)).toBe('5.0 MB')
    expect(formatFileSize(1024 ** 3)).toBe('1.0 GB')
  })
})

describe('formatScore', () => {
  it('保留四位小数', () => {
    expect(formatScore(0.94861)).toBe('0.9486')
    expect(formatScore('0.5')).toBe('0.5000')
  })

  it('非法值返回占位符', () => {
    expect(formatScore(undefined)).toBe('—')
    expect(formatScore('abc')).toBe('—')
  })
})

describe('formatLatency', () => {
  it('按毫秒与秒分级展示', () => {
    expect(formatLatency(12)).toBe('12 毫秒')
    expect(formatLatency(999)).toBe('999 毫秒')
    expect(formatLatency(1500)).toBe('1.5 秒')
    expect(formatLatency(0)).toBe('—')
    expect(formatLatency(null)).toBe('—')
  })
})

describe('truncate', () => {
  it('超长文本截断并加省略号', () => {
    expect(truncate('abcdef', 4)).toBe('abcd…')
    expect(truncate('abc', 4)).toBe('abc')
    expect(truncate('', 4)).toBe('')
    expect(truncate(null)).toBe('')
  })
})

describe('日期时间格式化', () => {
  it('非法时间返回占位符', () => {
    expect(formatDateTime(null)).toBe('—')
    expect(formatDateTime('not-a-date')).toBe('—')
    expect(formatTime(null)).toBe('')
    expect(formatTime('not-a-date')).toBe('')
  })

  it('有效时间按中文格式输出', () => {
    const value = '2026-10-17T08:05:00+08:00'
    expect(formatDateTime(value)).toMatch(/^2026-10-17 \d{2}:\d{2}$/)
    expect(formatTime(value)).toMatch(/^\d{2}:\d{2}$/)
  })

  it('相对时间对刚刚与非法值给出默认值', () => {
    expect(formatRelativeTime(null)).toBe('刚刚')
    expect(formatRelativeTime('not-a-date')).toBe('刚刚')
    expect(formatRelativeTime(new Date())).toBe('刚刚')
    expect(formatRelativeTime(Date.now() - 2 * 3600 * 1000)).toContain('小时')
  })
})
