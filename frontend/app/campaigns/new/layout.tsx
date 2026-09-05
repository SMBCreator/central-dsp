import type { Metadata } from "next"
import type { ReactNode } from "react"
import { PAGE_TITLES } from "@/lib/nav"

export const metadata: Metadata = { title: PAGE_TITLES["campaigns/new"] }

export default function Layout({ children }: { children: ReactNode }) {
  return children
}
