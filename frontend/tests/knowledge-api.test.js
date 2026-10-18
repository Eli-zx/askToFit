import { beforeEach, describe, expect, it, vi } from 'vitest'

const { http } = vi.hoisted(() => ({
  http: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

vi.mock('@/api/http', () => ({ default: http }))

import {
  getParserOptions,
  listDocuments,
  listParentChunks,
  uploadDocument,
} from '@/api/knowledge'

function markdownFile() {
  return new File(['# 退换货规则'], '退换货规则.md', {
    type: 'text/markdown',
  })
}

describe('knowledge api', () => {
  beforeEach(() => {
    Object.values(http).forEach((method) => method.mockReset())
    http.post.mockResolvedValue({ id: 1 })
  })

  it('sends force_new when explicitly enabled', async () => {
    await uploadDocument(7, markdownFile(), {
      chunkSize: 300,
      chunkOverlap: 60,
      embeddingMode: 'hybrid',
      forceNew: true,
    })

    const [url, formData] = http.post.mock.calls[0]
    expect(url).toBe('/knowledge_bases/7/documents')
    expect(formData.get('force_new')).toBe('true')
    expect(formData.get('chunk_size')).toBe('300')
    expect(formData.get('chunk_overlap')).toBe('60')
    expect(formData.get('embedding_mode')).toBe('hybrid')
  })

  it('sends mineru parser only when deep parsing is selected', async () => {
    await uploadDocument(7, markdownFile(), {
      chunkSize: 500,
      chunkOverlap: 80,
      embeddingMode: 'hybrid',
      parser: 'mineru',
    })

    const [, formData] = http.post.mock.calls[0]
    expect(formData.get('parser')).toBe('mineru')

    http.post.mockClear()
    await uploadDocument(7, markdownFile(), {
      chunkSize: 500,
      chunkOverlap: 80,
      embeddingMode: 'hybrid',
      parser: 'auto',
    })
    expect(http.post.mock.calls[0][1].get('parser')).toBeNull()
  })

  it('loads parser availability options', async () => {
    http.get.mockResolvedValue({ mineru: { available: false } })
    const payload = await getParserOptions()
    expect(http.get).toHaveBeenCalledWith('/parsers/options')
    expect(payload.mineru.available).toBe(false)
  })

  it('omits force_new by default so dedup stays enabled', async () => {
    await uploadDocument(7, markdownFile(), {
      chunkSize: 500,
      chunkOverlap: 80,
      embeddingMode: 'dense',
    })

    const [, formData] = http.post.mock.calls[0]
    expect(formData.get('force_new')).toBeNull()
  })

  it('passes document list filters through', async () => {
    http.get.mockResolvedValue([])
    await listDocuments(3, { include_history: true })
    expect(http.get).toHaveBeenCalledWith('/knowledge_bases/3/documents', {
      params: { include_history: true },
    })
  })

  it('loads parent chunks for a document', async () => {
    http.get.mockResolvedValue([])
    await listParentChunks(11)
    expect(http.get).toHaveBeenCalledWith('/documents/11/parents')
  })
})
