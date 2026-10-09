import { useEffect } from 'react'
import { useLocation } from 'react-router'

const PAGES: Array<[RegExp, string]> = [
  [/^\/$/, 'Quantum portfolios for the NIFTY 50'],
  [/^\/dashboard/, 'Dashboard'],
  [/^\/lab/, 'Portfolio Lab'],
  [/^\/runs\//, 'Run result'],
  [/^\/experiments/, 'Experiments'],
  [/^\/history/, 'History'],
  [/^\/guardrails/, 'Guardrails'],
]

/** The page's name for the top bar and the tab title. */
export const pageName = (path: string) => PAGES.find(([re]) => re.test(path))?.[1] ?? 'Page not found'

/** Sets the tab title from the path; run pages set their own once the run has loaded. */
export function useDocumentTitle() {
  const { pathname } = useLocation()
  useEffect(() => {
    if (!/^\/runs\//.test(pathname)) document.title = pathname === '/' ? `QuantFolio · ${pageName(pathname)}` : `${pageName(pathname)} · QuantFolio`
  }, [pathname])
}
