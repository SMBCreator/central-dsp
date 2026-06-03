module.exports = {
  content: [
    './index.html',
    './src/**/*.{vue,js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // Central DSP — light, syntermedia-inspired palette
        brand: {
          DEFAULT: '#18181b',
          hover:   '#27272a',
          soft:    '#f4f4f5',
        },
        accent: {
          DEFAULT: '#6366f1',
          hover:   '#4f46e5',
          soft:    '#eef2ff',
        },
        surface: {
          DEFAULT: '#ffffff',
          page:    '#fafafa',
          hover:   '#f4f4f5',
          border:  '#e4e4e7',
          divider: '#f0f0f0',
        },
        text: {
          primary:   '#18181b',
          secondary: '#71717a',
          muted:     '#a1a1aa',
          inverse:   '#ffffff',
        },
        positive: '#16a34a',
        negative: '#dc2626',
        warning:  '#d97706',
        info:     '#2563eb',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      fontSize: {
        '2xs': ['11px', '16px'],
        'xs':  ['12px', '16px'],
        'sm':  ['13px', '20px'],
        'base':['14px', '22px'],
        'md':  ['15px', '24px'],
        'lg':  ['16px', '24px'],
        'xl':  ['18px', '28px'],
        '2xl': ['22px', '32px'],
        '3xl': ['28px', '36px'],
      },
      boxShadow: {
        'card':   '0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)',
        'panel':  '0 4px 16px rgba(0,0,0,0.08)',
        'modal':  '0 20px 60px rgba(0,0,0,0.12)',
        'sm':     '0 1px 2px rgba(0,0,0,0.05)',
      },
      borderRadius: {
        'xs': '4px',
        'sm': '6px',
        'md': '8px',
        'lg': '12px',
        'xl': '16px',
      },
      transitionTimingFunction: {
        'smooth': 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
    },
  },
  plugins: [],
}
