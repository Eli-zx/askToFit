import { describe, expect, it } from 'vitest'

import { groupGapsByUser } from '@/utils/knowledge-gaps'

describe('groupGapsByUser', () => {
  it('按提交人分组并保持首次出现顺序', () => {
    const groups = groupGapsByUser([
      { id: 3, user_id: 2, username: 'bob', question: 'bob 的新问题' },
      { id: 2, user_id: 1, username: 'alice', question: 'alice 的问题' },
      { id: 1, user_id: 2, username: 'bob', question: 'bob 的旧问题' },
    ])

    expect(groups.map((group) => group.username)).toEqual(['bob', 'alice'])
    expect(groups[0].items.map((item) => item.id)).toEqual([3, 1])
    expect(groups[1].items.map((item) => item.id)).toEqual([2])
  })

  it('缺失用户名时显示占位符', () => {
    const groups = groupGapsByUser([{ id: 1, user_id: 9 }])

    expect(groups[0].username).toBe('—')
  })

  it('空列表返回空分组', () => {
    expect(groupGapsByUser([])).toEqual([])
    expect(groupGapsByUser(undefined)).toEqual([])
  })
})
