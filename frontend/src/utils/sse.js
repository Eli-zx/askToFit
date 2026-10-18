export class SSEError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'SSEError'
    this.status = status
  }
}

/**
 * 解析单个 SSE 事件块。后端事件格式：
 * event: delta
 * data: {"content":"..."}
 */
export function parseSSEBlock(block) {
  if (!block || !block.trim()) return null
  let event = 'message'
  const dataLines = []

  for (const rawLine of block.split('\n')) {
    const line = rawLine.replace(/\r$/, '')
    if (!line || line.startsWith(':')) continue
    if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }

  if (!dataLines.length) return null
  const dataText = dataLines.join('\n')
  let data = dataText
  try {
    data = JSON.parse(dataText)
  } catch {
    // 保留原始文本，兼容非 JSON 的 data 事件
  }
  return { event, data }
}

export function createSSEParser(onEvent) {
  let buffer = ''

  return {
    push(chunk) {
      buffer += chunk.replace(/\r\n/g, '\n')
      let boundary = buffer.indexOf('\n\n')
      while (boundary !== -1) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const parsed = parseSSEBlock(block)
        if (parsed) onEvent(parsed.event, parsed.data)
        boundary = buffer.indexOf('\n\n')
      }
    },
    flush() {
      if (buffer.trim()) {
        const parsed = parseSSEBlock(buffer)
        if (parsed) onEvent(parsed.event, parsed.data)
      }
      buffer = ''
    },
  }
}

async function readErrorResponse(response) {
  let message = `请求失败（${response.status}）`
  try {
    const payload = await response.json()
    const detail = payload?.detail
    if (typeof detail === 'string') {
      message = detail
    } else if (Array.isArray(detail)) {
      message = detail.map((item) => item?.msg || item?.message || '').filter(Boolean).join('；') || message
    }
  } catch {
    // 非 JSON 错误响应
  }
  return new SSEError(message, response.status)
}

/**
 * 使用 fetch 发送带 Authorization 的 SSE 请求并逐事件回调。
 */
export async function streamSSE({ url, token, payload, signal, onEvent }) {
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
    signal,
  })

  if (!response.ok) throw await readErrorResponse(response)
  if (!response.body) throw new SSEError('浏览器不支持流式响应', 0)

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  const parser = createSSEParser(onEvent)

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      parser.push(decoder.decode(value, { stream: true }))
    }
    parser.push(decoder.decode())
    parser.flush()
  } finally {
    reader.releaseLock()
  }
}
