import axios from 'axios'

import { API_BASE } from '@/utils/constants'
import { toErrorMessage } from '@/utils/errors'
import { clearSession, getToken } from '@/utils/storage'

const http = axios.create({
  baseURL: API_BASE,
  timeout: 60_000,
})

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
      clearSession()
      if (!window.location.pathname.startsWith('/login')) {
        const redirect = encodeURIComponent(
          `${window.location.pathname}${window.location.search}`,
        )
        window.location.assign(`/login?redirect=${redirect}`)
      }
    }

    error.friendlyMessage = toErrorMessage(error)
    return Promise.reject(error)
  },
)

export default http
