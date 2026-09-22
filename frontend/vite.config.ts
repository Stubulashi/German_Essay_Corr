import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vite 配置:开发服务器将 /api 请求代理到后端(8765)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
    },
  },
})
