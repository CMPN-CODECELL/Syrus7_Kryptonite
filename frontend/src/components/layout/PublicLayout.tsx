import { Outlet, ScrollRestoration } from 'react-router'
import { ButtonLink, IconArrowRight } from '../ui'
import { ApiBadge, Banners, Brand, Footer, NavigationProgress } from './Layout'
import { useDocumentTitle } from './title'

const SECTIONS = [
  { href: '#how', label: 'How it works' },
  { href: '#example', label: 'Example' },
  { href: '#guardrails', label: 'Guardrails' },
]

/** The public landing page: a slim top bar with an "Open dashboard" button and no sidebar. */
export function PublicLayout() {
  useDocumentTitle()
  return (
    <div className="flex min-h-dvh flex-col bg-canvas">
      <a href="#main" className="sr-only-focusable fixed top-2 left-2 z-50 rounded-md bg-ink px-3 py-2 text-sm text-white">
        Skip to content
      </a>
      <NavigationProgress />
      <header className="sticky top-0 z-40 border-b border-line bg-canvas/92 backdrop-blur supports-[backdrop-filter]:bg-canvas/80">
        <div className="mx-auto flex h-16 max-w-[1320px] items-center gap-6 px-4 sm:px-6">
          <Brand />
          <nav aria-label="Page sections" className="hidden md:block">
            <ul className="flex items-center gap-1">
              {SECTIONS.map((s) => (
                <li key={s.href}>
                  <a href={s.href} className="inline-flex min-h-10 items-center rounded-md px-3 text-sm font-medium text-ink-2 hover:bg-mist hover:text-ink">
                    {s.label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <span className="hidden sm:inline-flex">
              <ApiBadge />
            </span>
            <ButtonLink to="/dashboard" size="sm" iconRight={<IconArrowRight size={15} />} data-testid="nav-dashboard">
              Open dashboard
            </ButtonLink>
          </div>
        </div>
      </header>
      <div className="pt-3 empty:pt-0">
        <Banners />
      </div>
      <main id="main" tabIndex={-1} className="flex-1 outline-none">
        <Outlet />
      </main>
      <Footer />
      <ScrollRestoration />
    </div>
  )
}
