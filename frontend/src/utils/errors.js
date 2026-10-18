const NETWORK_MESSAGE = '网络连接异常，请确认后端服务已启动'
const DEFAULT_MESSAGE = '操作失败，请稍后重试'

function normalizeItem(item) {
  if (!item) return ''
  if (typeof item === 'string') return item
  const message = item.msg || item.message || item.detail || ''
  return String(message).replace(/^Value error,\s*/i, '')
}

/**
 * 将 FastAPI / Axios / Fetch 错误统一转换为可直接展示的中文提示。
 */
export function toErrorMessage(error, fallback = DEFAULT_MESSAGE) {
  if (!error) return fallback
  if (typeof error === 'string') return error || fallback
  if (error.name === 'AbortError') return '请求已取消'

  const detail =
    error.detail ??
    error.data?.detail ??
    error.response?.data?.detail ??
    error.response?.data?.message

  if (Array.isArray(detail)) {
    const messages = detail.map(normalizeItem).filter(Boolean)
    if (messages.length) return messages.join('；')
  }
  if (detail) return normalizeItem(detail)

  if (error.response?.status === 401) return '登录状态已失效，请重新登录'
  if (error.response?.status === 403) return '没有权限执行此操作'
  if (error.response?.status === 404) return '请求的资源不存在'
  if (error.response?.status === 413) return '文件超过大小限制'
  if (error.response?.status >= 500) return '服务暂时不可用，请稍后重试'

  if (error.code === 'ECONNABORTED') return '请求超时，请稍后重试'
  if (error.message === 'Failed to fetch' || error.request) return NETWORK_MESSAGE
  return error.message || fallback
}
