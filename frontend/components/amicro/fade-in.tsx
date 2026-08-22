"use client"

import type React from "react"
import { motion } from "framer-motion"

/* Amicro (Subhan-code/Amicro--Micro-transitions-) fade-in entrance */

interface FadeInProps {
  children: React.ReactNode
  duration?: number
  delay?: number
  className?: string
}

export function FadeIn({ children, duration = 0.5, delay = 0, className = "" }: FadeInProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration,
        delay,
        ease: [0.215, 0.61, 0.355, 1], // easeOutCubic
      }}
      className={className}
    >
      {children}
    </motion.div>
  )
}
