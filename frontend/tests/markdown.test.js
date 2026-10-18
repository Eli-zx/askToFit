import { describe, expect, it } from 'vitest'

import { renderMarkdown } from '@/utils/markdown'

describe('renderMarkdown', () => {
  it('空内容返回空字符串', () => {
    expect(renderMarkdown('')).toBe('')
    expect(renderMarkdown('   ')).toBe('')
    expect(renderMarkdown(null)).toBe('')
  })

  it('渲染基础 Markdown 结构', () => {
    const html = renderMarkdown('**加粗**\n\n- 第一项\n- 第二项')
    expect(html).toContain('<strong>加粗</strong>')
    expect(html).toContain('<li>第一项</li>')
  })

  it('移除 script 标签与事件属性', () => {
    const html = renderMarkdown(
      '<script>window.__xss = true</script>\n\n<img src="x" onerror="alert(1)">',
    )
    expect(html.toLowerCase()).not.toContain('<script')
    expect(html).not.toContain('onerror')
    expect(html).not.toContain('alert(1)')
  })

  it('移除 javascript: 协议链接', () => {
    const html = renderMarkdown('[点我](javascript:alert(1))')
    expect(html).not.toContain('javascript:')
  })

  it('保留正常链接与表格', () => {
    const html = renderMarkdown(
      '[帮助中心](https://example.com/help)\n\n| 时效 | 说明 |\n| --- | --- |\n| 3 天 | 电子发票 |',
    )
    expect(html).toContain('https://example.com/help')
    expect(html).toContain('<table>')
    expect(html).toContain('<td>3 天</td>')
  })
})
