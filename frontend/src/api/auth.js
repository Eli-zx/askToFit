import http from './http'

export function fetchCaptcha() {
  return http.get('/auth/captcha')
}

export function login(payload) {
  return http.post('/auth/login', payload)
}

export function register(payload) {
  return http.post('/auth/register', payload)
}

export function fetchCurrentUser() {
  return http.get('/auth/me')
}
