import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  SSEError,
  createSSEParser,
  parseSSEBlock,
  streamSSE,
} from '@/utils/sse'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('parseSSEBlock', () => {
  it('解析 event 与 JSON data', () => {
    const result = parseSSEBlock('event: delta\ndata: {"content":"你好"}')
    expect(result).toEqual({ event: 'delta', data: { content: '你好' } })
  })

  it('无 event 时默认为 message', () => {
    const result = parseSSEBlock('data: {"ok":true}')
    expect(result).toEqual({ event: 'message', data: { ok: true } })
  })

  it('兼容非 JSON 的 data 与多行 data', () => {
    const result = parseSSEBlock('event: notice\ndata: 第一行\ndata: 第二行')
    expect(result).toEqual({ event: 'notice', data: '第一行\n第二行' })
  })

  it('忽略注释行与空块', () => {
    expect(parseSSEBlock(': keep-alive')).toBeNull()
    expect(parseSSEBlock('   ')).toBeNull()
    expect(parseSSEBlock('')).toBeNull()
  })

  it('兼容 CRLF 换行', () => {
    const result = parseSSEBlock('event: done\r\ndata: {"content":"好"}\r\n')
    expect(result).toEqual({ event: 'done', data: { content: '好' } })
  })
})

describe('createSSEParser', () => {
  it('跨 chunk 拼接事件，只在空行处分发', () => {
    const events = []
    const parser = createSSEParser((event, data) => events.push({ event, data }))

    parser.push('event: meta\ndata: {"intent":"logis')
    expect(events).toHaveLength(0)

    parser.push('tics"}\n\nevent: delta\ndata: {"content":"满 199')
    expect(events).toEqual([{ event: 'meta', data: { intent: 'logistics' } }])

    parser.push(' 元包邮"}\n\n')
    expect(events).toHaveLength(2)
    expect(events[1]).toEqual({
      event: 'delta',
      data: { content: '满 199 元包邮' },
    })
  })

  it('将 CRLF 归一化，避免边界识别失败', () => {
    const events = []
    const parser = createSSEParser((event, data) => events.push({ event, data }))
    parser.push('event: status\r\ndata: {"stage":"retrieving"}\r\n\r\n')
    expect(events).toEqual([
      { event: 'status', data: { stage: 'retrieving' } },
    ])
  })

  it('flush 分发末尾未以空行结束的事件', () => {
    const events = []
    const parser = createSSEParser((event, data) => events.push({ event, data }))
    parser.push('event: done\ndata: {"content":"完成"}')
    expect(events).toHaveLength(0)
    parser.flush()
    expect(events).toEqual([{ event: 'done', data: { content: '完成' } }])
    parser.flush()
    expect(events).toHaveLength(1)
  })
})

function sseResponse(parts, status = 200) {
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      for (const part of parts) controller.enqueue(encoder.encode(part))
      controller.close()
    },
  })
  return new Response(stream, {
    status,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

describe('streamSSE', () => {
  it('携带 Bearer Token，并按顺序回调事件', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([
        'event: status\ndata: {"stage":"retrieving"}\n\n',
        'event: delta\ndata: {"content":"满 199 元',
        '包邮"}\n\nevent: done\ndata: {"content":"满 199 元包邮"}\n\n',
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const events = []
    await streamSSE({
      url: '/api/chat/conversations/1/messages/stream',
      token: 'demo-token',
      payload: { content: '满多少包邮？' },
      onEvent: (event, data) => events.push({ event, data }),
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/chat/conversations/1/messages/stream')
    expect(options.method).toBe('POST')
    expect(options.headers.Authorization).toBe('Bearer demo-token')
    expect(options.headers.Accept).toBe('text/event-stream')
    expect(JSON.parse(options.body)).toEqual({ content: '满多少包邮？' })

    expect(events.map((item) => item.event)).toEqual([
      'status',
      'delta',
      'done',
    ])
    expect(events[1].data.content).toBe('满 199 元包邮')
  })

  it('非 2xx 响应抛出带 detail 的 SSEError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: '会话不存在' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    await expect(
      streamSSE({
        url: '/api/chat/conversations/404/messages/stream',
        token: 'demo-token',
        payload: { content: '你好' },
        onEvent: () => {},
      }),
    ).rejects.toMatchObject({
      name: 'SSEError',
      message: '会话不存在',
      status: 404,
    })
  })

  it('校验错误数组转换为可读文本', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: [{ msg: '内容不能为空' }, { msg: '内容过长' }],
          }),
          { status: 422 },
        ),
      ),
    )

    const error = await streamSSE({
      url: '/api/chat/stream',
      payload: {},
      onEvent: () => {},
    }).catch((item) => item)

    expect(error).toBeInstanceOf(SSEError)
    expect(error.message).toBe('内容不能为空；内容过长')
    expect(error.status).toBe(422)
  })
})
