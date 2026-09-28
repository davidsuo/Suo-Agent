import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:10000',
        changeOrigin: true,
      },
      '/charts': {
        target: 'http://localhost:10000',
        changeOrigin: true,
      },
      '/images': {
        target: 'http://localhost:10000',  // 【新增】确保图片能正常加载
        changeOrigin: true,
      },
    },
  },
})