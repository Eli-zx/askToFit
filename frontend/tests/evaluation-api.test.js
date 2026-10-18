import { beforeEach, describe, expect, it, vi } from 'vitest'

const { http } = vi.hoisted(() => ({
  http: {
    get: vi.fn(),
    delete: vi.fn(),
  },
}))

vi.mock('@/api/http', () => ({ default: http }))

import {
  deleteEvaluationRun,
  fetchEvaluationOverview,
  fetchEvaluationTrends,
  getEvaluationRun,
  listEvaluationCases,
  listEvaluationRuns,
} from '@/api/evaluation'

describe('evaluation api', () => {
  beforeEach(() => {
    http.get.mockReset()
    http.delete.mockReset()
    http.get.mockResolvedValue({})
  })

  it('loads overview and returns response body', async () => {
    http.get.mockResolvedValue({ latest: { id: 2 }, total_runs: 2 })
    const payload = await fetchEvaluationOverview()
    expect(http.get).toHaveBeenCalledWith('/evaluations/overview')
    expect(payload.latest.id).toBe(2)
  })

  it('passes run filters and pagination through', async () => {
    await listEvaluationRuns({ limit: 10, offset: 20, strategy: 'hyde', status: 'success' })
    expect(http.get).toHaveBeenCalledWith('/evaluations/runs', {
      params: { limit: 10, offset: 20, strategy: 'hyde', status: 'success' },
    })
  })

  it('loads a single run', async () => {
    await getEvaluationRun(7)
    expect(http.get).toHaveBeenCalledWith('/evaluations/runs/7')
  })

  it('loads cases with failed-only filters', async () => {
    await listEvaluationCases(7, { limit: 50, offset: 0, failed_only: true })
    expect(http.get).toHaveBeenCalledWith('/evaluations/runs/7/cases', {
      params: { limit: 50, offset: 0, failed_only: true },
    })
  })

  it('loads trends with limit', async () => {
    await fetchEvaluationTrends(12)
    expect(http.get).toHaveBeenCalledWith('/evaluations/trends', {
      params: { limit: 12 },
    })
  })

  it('deletes a run by id', async () => {
    await deleteEvaluationRun(9)
    expect(http.delete).toHaveBeenCalledWith('/evaluations/runs/9')
  })
})
