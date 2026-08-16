import type { Metadata, Viewport } from 'next'
import { Inter, JetBrains_Mono } from 'next/font/google'
import { AppShell } from '@/components/app-shell'
import './globals.css'

const inter = Inter({ subsets: ['latin', 'cyrillic'], variable: '--font-inter' })
const jetbrains = JetBrains_Mono({ subsets: ['latin', 'cyrillic'], variable: '--font-jetbrains' })

export const metadata: Metadata = {
  title: 'CentralDSP — Платформа программатик-рекламы',
  description:
    'DSP-платформа для рынка СНГ: RTB-закупка, баннеры, нативная и видеореклама, аналитика, автоправила и AI-инструменты.',
}

export const viewport: Viewport = {
  themeColor: '#0c1017',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className={`bg-background ${inter.variable} ${jetbrains.variable}`}>
      <body className="font-sans antialiased">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  )
}
