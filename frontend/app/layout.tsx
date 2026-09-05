import type { Metadata, Viewport } from 'next'
import { Inter, JetBrains_Mono } from 'next/font/google'
import { AppShell } from '@/components/app-shell'
import './globals.css'

// Reference design uses Geist; Geist has no Cyrillic glyphs, Inter is the
// closest metric-compatible sans with full Cyrillic support.
const inter = Inter({ subsets: ['latin', 'cyrillic'], variable: '--font-inter' })
const jetbrains = JetBrains_Mono({ subsets: ['latin', 'cyrillic'], variable: '--font-jetbrains' })

export const metadata: Metadata = {
  title: {
    default: 'CentralDSP — Платформа программатик-рекламы',
    template: '%s — CentralDSP',
  },
  description:
    'DSP-платформа для рынка СНГ: RTB-закупка, баннеры, нативная и видеореклама, аналитика, автоправила и AI-инструменты.',
}

export const viewport: Viewport = {
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#ffffff' },
    { media: '(prefers-color-scheme: dark)', color: '#0f0f0f' },
  ],
}

// Тема и состояние сайдбара (dsp.sidebar) применяются до гидратации, чтобы не было мерцания.
const themeInit = `(function(){try{var t=localStorage.getItem('theme');if(t==='dark'||(!t&&window.matchMedia('(prefers-color-scheme: dark)').matches)){document.documentElement.classList.add('dark')}if(localStorage.getItem('dsp.sidebar')==='collapsed'){document.documentElement.classList.add('sidebar-collapsed')}}catch(e){}})()`

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className={`bg-background ${inter.variable} ${jetbrains.variable}`} suppressHydrationWarning>
      <head>
        {/* Applies saved theme before hydration to avoid a flash */}
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
      </head>
      <body className="font-sans antialiased">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  )
}
