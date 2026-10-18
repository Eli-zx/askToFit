import { API_BASE } from '@/utils/constants'
import { streamSSE } from '@/utils/sse'

import http from './http'

export function fetchChatOptions() {
  return http.get('/chat/options')
}

export function listConversations(includeArchived = false) {
  return http.get('/chat/conversations', {
    params: includeArchived ? { include_archived: true } : undefined,
  })
}

export function createConversation(payload) {
  return http.post('/chat/conversations', payload)
}

export function getConversation(conversationId) {
  return http.get(`/chat/conversations/${conversationId}`)
}

export function updateConversation(conversationId, payload) {
  return http.patch(`/chat/conversations/${conversationId}`, payload)
}

export function deleteConversation(conversationId) {
  return http.delete(`/chat/conversations/${conversationId}`)
}

export function listMessages(conversationId, limit = 100) {
  return http.get(`/chat/conversations/${conversationId}/messages`, {
    params: { limit },
  })
}

export function archiveConversation(conversationId) {
  return http.post(`/chat/conversations/${conversationId}/archive`)
}

export function unarchiveConversation(conversationId) {
  return http.delete(`/chat/conversations/${conversationId}/archive`)
}

export function listKnowledgeGaps({ limit = 50, offset = 0, status, intent, q } = {}) {
  return http.get('/chat/knowledge-gaps', {
    params: { limit, offset, status, intent, q },
  })
}

export function fetchKnowledgeGapStats(days = 14) {
  return http.get('/chat/knowledge-gaps/stats', { params: { days } })
}

export function updateKnowledgeGap(gapId, payload) {
  return http.patch(`/chat/knowledge-gaps/${gapId}`, payload)
}

export function streamMessage(conversationId, content, token, signal, onEvent) {
  return streamSSE({
    url: `${API_BASE}/chat/conversations/${conversationId}/messages/stream`,
    token,
    payload: { content },
    signal,
    onEvent,
  })
}
