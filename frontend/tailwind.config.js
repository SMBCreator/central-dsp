module.exports = {
  content: [
    './index.html',
    './src/**/*.{vue,js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // Central DSP cyberpunk palette
        brand: {
          DEFAULT: '#00d4ff',
          dark:    '#0099bb',
          glow:    'rgba(0,212,255,0.15)',
        },
        surface: {
          DEFAULT: '#0d1117',
          card:    '#161b22',
          border:  '#30363d',
        },
        success: '#39d353',
        warning: '#e3b341',
        danger:  '#f85149',
        muted:   '#8b949e',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
