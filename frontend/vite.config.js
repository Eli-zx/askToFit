import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig, loadEnv } from 'vite'

const API_PROXY = {
  '/api': {
    target: 'http://127.0.0.1:8000',
    changeOrigin: true,
  },
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // 额外允许的访问域名，逗号分隔；localhost 与 IP 默认允许。
  const allowedHosts = (env.VITE_ALLOWED_HOSTS || 'asktofit.elizx.xyz')
    .split(',')
    .map((host) => host.trim())
    .filter(Boolean)

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      strictPort: true,
      allowedHosts: allowedHosts.length ? allowedHosts : undefined,
      proxy: API_PROXY,
    },
    preview: {
      host: '0.0.0.0',
      // 预览与 dev 共用 5173：端口被占时直接失败而不是换端口
      port: 5173,
      strictPort: true,
      // 构建产物预览必须有与 dev 相同的 /api 代理
      allowedHosts: allowedHosts.length ? allowedHosts : undefined,
      proxy: API_PROXY,
    },
    test: {
      environment: 'jsdom',
      include: ['tests/**/*.test.js'],
      globals: true,
    },
  }
})
