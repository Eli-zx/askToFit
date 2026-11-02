/**
 * 按提交人分组缺口记录，组顺序沿用记录首次出现的顺序（接口按时间倒序返回）。
 */
export function groupGapsByUser(items) {
  const groups = []
  const index = new Map()

  for (const item of items || []) {
    const key = item.user_id ?? 0
    let group = index.get(key)
    if (!group) {
      group = {
        key,
        userId: item.user_id,
        username: item.username || '—',
        items: [],
      }
      index.set(key, group)
      groups.push(group)
    }
    group.items.push(item)
  }

  return groups
}
