const { defineConfig } = require('@vitejs/plugin-vue')
const vue = require('@vitejs/plugin-vue')

module.exports = defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api':     'http://backend:8000',
      '/s':       'http://backend:8000',
      '/c':       'http://backend:8000',
      '/track':   'http://backend:8000',
      '/px':      'http://backend:8000',
      '/postback':'http://backend:8000',
      '/health':  'http://backend:8000',
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  }
})
