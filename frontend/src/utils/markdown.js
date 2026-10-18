import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.setOptions({
  gfm: true,
  breaks: true,
})

/**
 * 将模型返回的 Markdown 渲染为安全的 HTML，避免注入脚本或事件属性。
 */
export function renderMarkdown(text) {
  const source = String(text || '')
  if (!source.trim()) return ''
  const html = marked.parse(source, { async: false })
  return DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    ADD_ATTR: ['target', 'rel'],
  })
}
