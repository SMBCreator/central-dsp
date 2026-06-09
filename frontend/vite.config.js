const { defineConfig } = require('@vitejs/plugin-vue')
const vue = require('@vitejs/plugin-vue')

module.exports = defineConfig({
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
