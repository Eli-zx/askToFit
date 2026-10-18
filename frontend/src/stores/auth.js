import { defineStore } from 'pinia'

import { fetchCurrentUser } from '@/api/auth'
import { clearSession, getStoredUser, getToken, saveSession, saveUser } from '@/utils/storage'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: getToken(),
    user: getStoredUser(),
    loading: false,
  }),

  getters: {
    isAuthenticated: (state) => Boolean(state.token),
    displayName: (state) => state.user?.nickname || state.user?.username || '访客',
    initials: (state) => {
      const name = state.user?.nickname || state.user?.username || 'A'
      return name.slice(0, 1).toUpperCase()
    },
  },

  actions: {
    applySession(session) {
      saveSession(session)
      this.token = session.access_token
      this.user = session.user
    },

    async loadProfile() {
      if (!this.token) return null
      this.loading = true
      try {
        const user = await fetchCurrentUser()
        saveUser(user)
        this.user = user
        return user
      } finally {
        this.loading = false
      }
    },

    signOut() {
      clearSession()
      this.token = ''
      this.user = null
    },
  },
})
