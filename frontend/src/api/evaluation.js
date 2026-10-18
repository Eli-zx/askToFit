import http from './http'

export function fetchEvaluationOverview() {
  return http.get('/evaluations/overview')
}

export function listEvaluationRuns(params = {}) {
  return http.get('/evaluations/runs', { params })
}

export function getEvaluationRun(runId) {
  return http.get(`/evaluations/runs/${runId}`)
}

export function listEvaluationCases(runId, params = {}) {
  return http.get(`/evaluations/runs/${runId}/cases`, { params })
}

export function fetchEvaluationTrends(limit = 30) {
  return http.get('/evaluations/trends', { params: { limit } })
}

export function deleteEvaluationRun(runId) {
  return http.delete(`/evaluations/runs/${runId}`)
}
