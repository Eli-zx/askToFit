import { describe, expect, it } from 'vitest'

import {
  buildConversationPayload,
  buildDefaultConversationPayload,
  clampTemperature,
  normalizeKbIds,
  pickDefaultKnowledgeBase,
} from '@/utils/chat'

const civilCode = { id: 17, name: '民法典', is_public: true }
const apparelPublic = { id: 7, name: '服装售前知识库（2026 春夏）', is_public: true }
const apparelPrivate = { id: 1, name: '服装售前知识库', is_public: false }

describe('buildDefaultConversationPayload', () => {
  it('uses the first available knowledge base when none is preferred', () => {
    expect(buildDefaultConversationPayload([{ id: 7 }, { id: 9 }])).toEqual({
      kb_ids: [7],
    })
  })

  it('skips entries without a usable id', () => {
    expect(
      buildDefaultConversationPayload([{ id: null }, { id: '3' }]),
    ).toEqual({ kb_ids: [3] })
  })

  it('returns null when no knowledge base is available', () => {
    expect(buildDefaultConversationPayload([])).toBeNull()
    expect(buildDefaultConversationPayload(null)).toBeNull()
    expect(buildDefaultConversationPayload([{ id: 'abc' }])).toBeNull()
  })

  it('prefers the apparel knowledge base over civil code', () => {
    expect(
      buildDefaultConversationPayload([civilCode, apparelPublic]),
    ).toEqual({ kb_ids: [7] })
  })

  it('prefers the public apparel knowledge base among apparel libraries', () => {
    expect(
      buildDefaultConversationPayload([apparelPrivate, civilCode, apparelPublic]),
    ).toEqual({ kb_ids: [7] })
  })
})

describe('buildConversationPayload', () => {
  it('uses explicit knowledge base ids and temperature', () => {
    expect(
      buildConversationPayload([civilCode, apparelPublic], {
        kbIds: ['17', 7],
        temperature: 0.6,
      }),
    ).toEqual({ kb_ids: [17, 7], temperature: 0.6 })
  })

  it('deduplicates ids and drops invalid values', () => {
    expect(
      buildConversationPayload([apparelPublic], {
        kbIds: [7, '7', null, 'abc', -1, 0],
      }),
    ).toEqual({ kb_ids: [7] })
  })

  it('falls back to the default knowledge base when selection is empty', () => {
    expect(
      buildConversationPayload([civilCode, apparelPublic], {
        kbIds: [],
        temperature: 0.3,
      }),
    ).toEqual({ kb_ids: [7], temperature: 0.3 })
  })

  it('caps the selection at ten knowledge bases', () => {
    const ids = Array.from({ length: 12 }, (_, index) => index + 1)
    expect(buildConversationPayload([], { kbIds: ids })).toEqual({
      kb_ids: ids.slice(0, 10),
    })
  })

  it('returns null when neither selection nor fallback exists', () => {
    expect(buildConversationPayload([], { kbIds: [] })).toBeNull()
  })

  it('omits temperature when it is not provided', () => {
    expect(buildConversationPayload([apparelPublic])).toEqual({ kb_ids: [7] })
  })
})

describe('clampTemperature', () => {
  it('clamps and rounds to one decimal place', () => {
    expect(clampTemperature(-1)).toBe(0)
    expect(clampTemperature(3)).toBe(2)
    expect(clampTemperature(0.37)).toBe(0.4)
  })

  it('falls back for non-numeric values', () => {
    expect(clampTemperature('abc')).toBe(0.3)
    expect(clampTemperature(null, 0.5)).toBe(0.5)
  })
})

describe('normalizeKbIds', () => {
  it('keeps unique positive ids only', () => {
    expect(normalizeKbIds([3, '3', 0, null, 'x', '4'])).toEqual([3, 4])
    expect(normalizeKbIds(null)).toEqual([])
  })
})

describe('pickDefaultKnowledgeBase', () => {
  it('returns null for missing or invalid lists', () => {
    expect(pickDefaultKnowledgeBase(null)).toBeNull()
    expect(pickDefaultKnowledgeBase([])).toBeNull()
    expect(pickDefaultKnowledgeBase([{ id: 'abc' }])).toBeNull()
  })

  it('does not use civil code when an apparel library exists', () => {
    expect(pickDefaultKnowledgeBase([civilCode, apparelPublic])).toBe(
      apparelPublic,
    )
  })

  it('falls back to the first usable library when no apparel library exists', () => {
    expect(pickDefaultKnowledgeBase([civilCode, { id: 9, name: '其他' }])).toBe(
      civilCode,
    )
  })

  it('keeps the original object so the conversation payload can convert the id', () => {
    const picked = pickDefaultKnowledgeBase([{ id: '7', name: '服装库', is_public: true }])
    expect(picked.id).toBe('7')
    expect(buildDefaultConversationPayload([{ id: '7', name: '服装库' }])).toEqual({
      kb_ids: [7],
    })
  })
})
