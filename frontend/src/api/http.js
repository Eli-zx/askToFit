import axios from 'axios'

import { API_BASE } from '@/utils/constants'
import { toErrorMessage } from '@/utils/errors'
import { clearSession, getToken } from '@/utils/storage'

const http = axios.create({
  baseURL: API_BASE,
  timeout: 60_000,
})

// 并发请求同时收到 401 时只跳转一次，避免多次整页导航叠加。
let redirecting = false

/**
 * 清理登录态并在需要时跳转登录页。
 * 没有本地 token 时交给路由守卫处理，避免未登录访问公开页也被整页重载。
 */
export function redirectToLoginOnce() {
  if (redirecting) return
  const hadToken = Boolean(getToken())
  clearSession()
  if (!hadToken) return
  if (window.location.pathname.startsWith('/login')) return
  redirecting = true
  const redirect = encodeURIComponent(
    `${window.location.pathname}${window.location.search}`,
  )
  window.location.replace(`/login?redirect=${redirect}`)
}

http.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const status = error.response?.status
    const url = error.config?.url || ''
    const isAuthRequest = url.includes('/auth/login') || url.includes('/auth/register')

    if (status === 401 && !isAuthRequest) {
      if (error.config?.skipAuthRedirect) {
        clearSession()
      } else {
        redirectToLoginOnce()
      }
    }

    error.friendlyMessage = toErrorMessage(error)
    return Promise.reject(error)
  },
)

export default http
