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
  archiveConversation,
  fetchKnowledgeGapStats,
  listConversations,
  listKnowledgeGaps,
  unarchiveConversation,
  updateKnowledgeGap,
} from '@/api/chat'

describe('chat api', () => {
  beforeEach(() => {
    Object.values(http).forEach((method) => method.mockReset())
    http.get.mockResolvedValue([])
    http.post.mockResolvedValue({})
    http.patch.mockResolvedValue({})
    http.delete.mockResolvedValue({})
  })

  it('requests active conversations by default', async () => {
    await listConversations()
    expect(http.get).toHaveBeenCalledWith('/chat/conversations', {
      params: undefined,
    })
  })

  it('includes archived conversations when requested', async () => {
    await listConversations(true)
    expect(http.get).toHaveBeenCalledWith('/chat/conversations', {
      params: { include_archived: true },
    })
  })

  it('archives and restores a conversation', async () => {
    await archiveConversation(12)
    expect(http.post).toHaveBeenCalledWith('/chat/conversations/12/archive')

    await unarchiveConversation(12)
    expect(http.delete).toHaveBeenCalledWith('/chat/conversations/12/archive')
  })

  it('passes knowledge gap filters through', async () => {
    await listKnowledgeGaps({
      limit: 20,
      offset: 40,
      status: 'open',
      intent: 'logistics',
      q: '包邮',
    })

    expect(http.get).toHaveBeenCalledWith('/chat/knowledge-gaps', {
      params: {
        limit: 20,
        offset: 40,
        status: 'open',
        intent: 'logistics',
        q: '包邮',
      },
    })
  })

  it('loads knowledge gap stats for the requested window', async () => {
    http.get.mockResolvedValue({ total: 3, by_date: [], by_intent: [] })
    const payload = await fetchKnowledgeGapStats(7)
    expect(http.get).toHaveBeenCalledWith('/chat/knowledge-gaps/stats', {
      params: { days: 7 },
    })
    expect(payload.total).toBe(3)
  })

  it('updates gap status and note', async () => {
    await updateKnowledgeGap(5, { status: 'resolved', note: '已补充文档' })
    expect(http.patch).toHaveBeenCalledWith('/chat/knowledge-gaps/5', {
      status: 'resolved',
      note: '已补充文档',
    })
  })
})
