import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    // 範例場景配置在 backend/（src/site/layout.ts 匯入）。 / The example site layout lives in backend/.
    fs: { allow: ['.', '../backend/layout.example.json'] },
    proxy: {
      '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true },
    },
  },
  build: {
    rollupOptions: {
      input: {
        simulator: 'index.html',
        preview3d: '3d.html',
        palette: 'palette.html',
      },
    },
  },
  test: {
    environment: 'node',
  },
})
