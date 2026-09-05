import type { Metadata } from "next"
import type { ReactNode } from "react"
import { PAGE_TITLES } from "@/lib/nav"

// template — для вложенных сегментов (new, [id]); без него они теряют суффикс.
export const metadata: Metadata = {
  title: { default: PAGE_TITLES["campaigns"], template: "%s — CentralDSP" },
}

export default function Layout({ children }: { children: ReactNode }) {
  return children
}
