import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api':      'http://localhost:8777',
      '/s':        'http://localhost:8777',
      '/c':        'http://localhost:8777',
      '/track':    'http://localhost:8777',
      '/px':       'http://localhost:8777',
      '/postback': 'http://localhost:8777',
      '/health':   'http://localhost:8777',
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  }
})
