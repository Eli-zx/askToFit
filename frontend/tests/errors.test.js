import { describe, expect, it } from 'vitest'

import { toErrorMessage } from '@/utils/errors'

describe('toErrorMessage', () => {
  it('无错误时返回兜底文案', () => {
    expect(toErrorMessage(null)).toBe('操作失败，请稍后重试')
    expect(toErrorMessage(undefined, '加载失败')).toBe('加载失败')
  })

  it('直接使用字符串错误', () => {
    expect(toErrorMessage('知识库不存在')).toBe('知识库不存在')
  })

  it('识别主动取消请求', () => {
    expect(toErrorMessage({ name: 'AbortError' })).toBe('请求已取消')
  })

  it('解析 Axios 响应中的 detail', () => {
    expect(
      toErrorMessage({ response: { data: { detail: '用户名已存在' } } }),
    ).toBe('用户名已存在')
  })

  it('将 FastAPI 校验数组逐条拼接，并去掉 Value error 前缀', () => {
    const error = {
      response: {
        data: {
          detail: [
            { loc: ['body', 'password'], msg: 'Value error, 密码长度至少 8 位' },
            { msg: '两次输入的密码不一致' },
          ],
        },
      },
    }
    expect(toErrorMessage(error)).toBe(
      '密码长度至少 8 位；两次输入的密码不一致',
    )
  })

  it('非法参数数组中缺失 msg 的项会被忽略', () => {
    const error = {
      detail: [{ type: 'missing' }, { message: '必要参数缺失' }],
    }
    expect(toErrorMessage(error)).toBe('必要参数缺失')
  })

  it('缺少 detail 时回退到 HTTP 状态文案', () => {
    expect(toErrorMessage({ response: { status: 401 } })).toBe(
      '登录状态已失效，请重新登录',
    )
    expect(toErrorMessage({ response: { status: 403 } })).toBe(
      '没有权限执行此操作',
    )
    expect(toErrorMessage({ response: { status: 404 } })).toBe(
      '请求的资源不存在',
    )
    expect(toErrorMessage({ response: { status: 413 } })).toBe(
      '文件超过大小限制',
    )
    expect(toErrorMessage({ response: { status: 502 } })).toBe(
      '服务暂时不可用，请稍后重试',
    )
  })

  it('区分超时与网络中断', () => {
    expect(toErrorMessage({ code: 'ECONNABORTED' })).toBe('请求超时，请稍后重试')
    expect(toErrorMessage({ message: 'Failed to fetch' })).toBe(
      '网络连接异常，请确认后端服务已启动',
    )
    expect(toErrorMessage({ request: {} })).toBe(
      '网络连接异常，请确认后端服务已启动',
    )
  })

  it('未知错误使用 message 或兜底文案', () => {
    expect(toErrorMessage({ message: '未知异常' })).toBe('未知异常')
    expect(toErrorMessage({}, '默认错误')).toBe('默认错误')
  })
})
