import http from './http'

export function listKnowledgeBases() {
  return http.get('/knowledge_bases')
}

export function createKnowledgeBase(payload) {
  return http.post('/knowledge_bases', payload)
}

export function getKnowledgeBase(kbId) {
  return http.get(`/knowledge_bases/${kbId}`)
}

export function updateKnowledgeBase(kbId, payload) {
  return http.patch(`/knowledge_bases/${kbId}`, payload)
}

export function deleteKnowledgeBase(kbId) {
  return http.delete(`/knowledge_bases/${kbId}`)
}

export function listDocuments(kbId, params = {}) {
  return http.get(`/knowledge_bases/${kbId}/documents`, { params })
}

export function uploadDocument(kbId, file, options = {}, onProgress) {
  const formData = new FormData()
  formData.append('file', file)
  if (options.chunkSize != null) formData.append('chunk_size', options.chunkSize)
  if (options.chunkOverlap != null) formData.append('chunk_overlap', options.chunkOverlap)
  if (options.embeddingMode) formData.append('embedding_mode', options.embeddingMode)
  if (options.forceNew) formData.append('force_new', 'true')
  if (options.parser === 'mineru') formData.append('parser', 'mineru')

  return http.post(`/knowledge_bases/${kbId}/documents`, formData, {
    timeout: 120_000,
    onUploadProgress: (event) => {
      if (!onProgress || !event.total) return
      onProgress(Math.min(100, Math.round((event.loaded / event.total) * 100)))
    },
  })
}

export function getParserOptions() {
  return http.get('/parsers/options')
}

export function getDocument(docId) {
  return http.get(`/documents/${docId}`)
}

export function reindexDocument(docId) {
  return http.post(`/documents/${docId}/reindex`)
}

export function deleteDocument(docId) {
  return http.delete(`/documents/${docId}`)
}

export function listChunks(docId, params = {}) {
  return http.get(`/documents/${docId}/chunks`, { params })
}

export function listParentChunks(docId) {
  return http.get(`/documents/${docId}/parents`)
}

export function updateChunk(uid, content) {
  return http.patch(`/chunks/${uid}`, { content })
}

export function deleteChunk(uid) {
  return http.delete(`/chunks/${uid}`)
}
