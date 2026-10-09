import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, ScrollRestoration, isRouteErrorResponse, useLocation, useNavigate, useNavigation, useRouteError } from 'react-router'
import { DISCLAIMER } from '../../api/contract.gen'
import { useDataStatus, useHealth, useOnline } from '../../api/hooks'
import { formatDate } from '../../lib/format'
import { Badge, ButtonLink, ErrorState, IconAlert, IconDatabase, IconMenu, IconWifiOff, IconX, LoadingBlock, Notice } from '../ui'

const NAV = [
  { to: '/', label: 'Overview', end: true },
  { to: '/lab', label: 'Portfolio Lab' },
  { to: '/experiments', label: 'Experiments' },
  { to: '/history', label: 'History' },
]

function Brand() {
  return (
    <Link to="/" className="flex min-h-11 items-center gap-2.5 rounded-md pr-2 text-ink" aria-label="QuantFolio home">
      <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
        <rect x="1" y="1" width="30" height="30" rx="8" fill="#1f48d6" />
        <ellipse cx="16" cy="16" rx="11" ry="4.6" fill="none" stroke="#fff" strokeOpacity=".55" strokeWidth="1.3" transform="rotate(-24 16 16)" />
        <rect x="11.5" y="11.5" width="9" height="9" rx="2" fill="#fff" transform="rotate(45 16 16)" />
        <circle cx="25.2" cy="11.6" r="2" fill="#5ed1c9" />
      </svg>
      <span className="font-display text-[1.15rem] font-semibold tracking-tight">QuantFolio</span>
    </Link>
  )
}

function navClass({ isActive }: { isActive: boolean }) {
  return `flex min-h-11 items-center rounded-md px-3 text-sm font-medium transition-colors pointer-fine:min-h-9 ${
    isActive ? 'bg-cobalt-50 text-cobalt-800' : 'text-ink-2 hover:bg-mist-2 hover:text-ink'
  }`
}

function DataBadge() {
  const ds = useDataStatus()
  if (!ds.data) return null
  const a = ds.data.active
  const tone = a.stale ? 'amber' : a.label === 'synthetic test' ? 'amber' : 'neutral'
  return (
    <Badge tone={tone} icon={a.stale ? <IconAlert size={13} /> : <IconDatabase size={13} />} title={`${a.source}; ${a.n_symbols} symbols; ${a.first_date} to ${a.last_date}`}>
      <span className="hidden sm:inline">Data:&nbsp;</span>
      {a.label}
      {a.stale ? ' (stale)' : ''}
      <span className="hidden lg:inline">&nbsp;· to {formatDate(a.last_date)}</span>
    </Badge>
  )
}

function ApiBadge() {
  const health = useHealth()
  const online = useOnline()
  if (!online) return <Badge tone="amber" icon={<IconWifiOff size={13} />}>Offline</Badge>
  if (health.isError) return <Badge tone="danger" icon={<IconX size={13} />}>API unreachable</Badge>
  if (health.isPending) return null
  return (
    <Badge tone="teal" icon={<span className="qf-pulse-dot inline-block h-1.5 w-1.5 rounded-full bg-teal" aria-hidden="true" />} title={`API ${health.data.version}, schema ${health.data.schema_version}`}>
      API online
    </Badge>
  )
}

function Banners() {
  const online = useOnline()
  const health = useHealth()
  const ds = useDataStatus()
  const active = ds.data?.active
  return (
    <div className="mx-auto flex w-full max-w-[1320px] flex-col gap-2 px-4 empty:hidden sm:px-6" aria-live="polite">
      {!online ? (
        <Notice tone="warning" icon={<IconWifiOff size={18} />} title="You are offline">
          Showing what was already loaded. Live run progress resumes automatically when the connection returns.
        </Notice>
      ) : health.isError ? (
        <Notice tone="danger" icon={<IconAlert size={18} />} title="The QuantFolio API is not reachable">
          Requests to <code className="font-mono text-xs">/api</code> are failing. Start the backend and this page will reconnect on its own.
        </Notice>
      ) : null}
      {active?.stale ? (
        <Notice tone="warning" icon={<IconAlert size={18} />} title="Market data is stale">
          {active.stale_reason ?? 'The cached dataset could not be refreshed.'} Data runs to {formatDate(active.last_date)}.
        </Notice>
      ) : null}
      {active?.label === 'synthetic test' ? (
        <Notice tone="warning" icon={<IconAlert size={18} />} title="Synthetic test data">
          The active dataset is generated for testing. Results are not based on market prices.
        </Notice>
      ) : null}
    </div>
  )
}

function NavigationProgress() {
  const nav = useNavigation()
  if (nav.state === 'idle') return null
  return (
    <div className="fixed inset-x-0 top-0 z-50 h-0.5 overflow-hidden bg-cobalt-100" role="progressbar" aria-label="Loading page">
      <div className="qf-indeterminate h-full w-1/3 bg-cobalt" />
    </div>
  )
}

const TITLES: Array<[RegExp, string]> = [
  [/^\/$/, 'Overview'],
  [/^\/lab/, 'Portfolio Lab'],
  [/^\/runs\//, 'Run'],
  [/^\/experiments/, 'Experiments'],
  [/^\/history/, 'History'],
]

export function Layout() {
  const loc = useLocation()
  // The mobile menu belongs to the page it was opened on, so navigating closes it.
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const menuOpen = menuFor === loc.pathname

  useEffect(() => {
    const t = TITLES.find(([re]) => re.test(loc.pathname))?.[1] ?? 'Not found'
    if (!/^\/runs\//.test(loc.pathname)) document.title = `${t} · QuantFolio`
  }, [loc.pathname])

  return (
    <div className="flex min-h-dvh flex-col bg-canvas">
      <a href="#main" className="sr-only-focusable fixed top-2 left-2 z-50 rounded-md bg-ink px-3 py-2 text-sm text-white">
        Skip to content
      </a>
      <NavigationProgress />
      <header className="sticky top-0 z-40 border-b border-line bg-canvas/92 backdrop-blur supports-[backdrop-filter]:bg-canvas/80">
        <div className="mx-auto flex h-16 max-w-[1320px] items-center gap-3 px-4 sm:px-6">
          <Brand />
          <nav aria-label="Main" className="ml-4 hidden items-center gap-1 md:flex">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end} className={navClass}>
                {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex min-w-0 items-center gap-2">
            <div className="hidden min-w-0 items-center gap-2 sm:flex">
              <DataBadge />
            </div>
            <ApiBadge />
            <button
              type="button"
              className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-md text-ink-2 hover:bg-mist-2 md:hidden"
              aria-expanded={menuOpen}
              aria-controls="mobile-nav"
              onClick={() => setMenuFor((o) => (o === loc.pathname ? null : loc.pathname))}
            >
              {menuOpen ? <IconX size={20} /> : <IconMenu size={20} />}
              <span className="sr-only">{menuOpen ? 'Close menu' : 'Open menu'}</span>
            </button>
          </div>
        </div>
        {menuOpen ? (
          <nav id="mobile-nav" aria-label="Main" className="border-t border-line bg-canvas px-4 pb-3 md:hidden">
            <ul className="flex flex-col gap-1 pt-2">
              {NAV.map((n) => (
                <li key={n.to}>
                  <NavLink to={n.to} end={n.end} className={navClass}>
                    {n.label}
                  </NavLink>
                </li>
              ))}
            </ul>
            <div className="mt-2 sm:hidden">
              <DataBadge />
            </div>
          </nav>
        ) : null}
      </header>
      <div className="pt-3 empty:pt-0">
        <Banners />
      </div>
      <main id="main" tabIndex={-1} className="min-h-dvh flex-1 outline-none">
        <Outlet />
      </main>
      <Footer />
      <ScrollRestoration />
    </div>
  )
}

function Footer() {
  const health = useHealth()
  return (
    <footer className="mt-16 border-t border-line bg-mist">
      <div className="mx-auto flex max-w-[1320px] flex-col gap-3 px-4 py-8 text-sm text-muted sm:px-6 md:flex-row md:items-start md:justify-between">
        <div className="max-w-prose">
          <p className="font-semibold text-ink-2">{DISCLAIMER}</p>
          <p className="mt-1 leading-relaxed">
            QuantFolio compares QAOA circuits run on a simulator with classical solvers on the same constrained problem. Historical
            estimates do not predict future returns. Universe membership is today&apos;s NIFTY 50, so past results carry survivorship
            bias.
          </p>
        </div>
        <p className="shrink-0 font-mono text-xs">
          {health.data ? `API ${health.data.version} · schema ${health.data.schema_version}` : 'API version unknown'}
        </p>
      </div>
    </footer>
  )
}

const CHUNK_ERROR = /dynamically imported module|Importing a module script failed|error loading dynamically/i

/** Rendered while the first code-split view downloads on a cold start, before the layout can render. */
export function AppLoading() {
  return (
    <div className="mx-auto min-h-dvh max-w-[1320px] px-4 py-10 sm:px-6">
      <LoadingBlock label="Loading QuantFolio" rows={4} />
    </div>
  )
}

/** Error boundary for one view: the header, navigation and footer stay usable around it. */
export function ViewError() {
  const err = useRouteError()
  const navigate = useNavigate()
  const location = useLocation()
  if (isRouteErrorResponse(err) && err.status === 404) return <RouteError />
  const chunk = err instanceof Error && CHUNK_ERROR.test(err.message)
  // Re-entering the same location resets the boundary and renders the view again; a failed download needs a reload.
  const retry = chunk
    ? () => window.location.reload()
    : () => navigate({ pathname: location.pathname, search: location.search, hash: location.hash }, { replace: true, state: location.state })
  return (
    <div className="mx-auto max-w-[1320px] px-4 py-10 sm:px-6" data-testid="view-error">
      <ErrorState title={chunk ? 'This page could not be loaded' : 'This view stopped working'} onRetry={retry}>
        {chunk
          ? 'Part of the app failed to download, usually because the connection dropped or the app was updated.'
          : 'Something unexpected happened while showing this page. The rest of the app still works; try again or go back to the overview.'}
      </ErrorState>
    </div>
  )
}

export function RouteError() {
  const err = useRouteError()
  const notFound = isRouteErrorResponse(err) && err.status === 404
  const chunk = err instanceof Error && CHUNK_ERROR.test(err.message)
  return (
    <div className="mx-auto max-w-xl px-4 py-24 text-center">
      <p className="eyebrow">{notFound ? '404' : 'Error'}</p>
      <h1 className="mt-2 font-display text-3xl font-semibold text-ink">{notFound ? 'Page not found' : chunk ? 'This page could not be loaded' : 'Something broke on this page'}</h1>
      <p className="mt-3 text-muted">
        {chunk
          ? 'Part of the app failed to download, usually because the connection dropped or the app was updated. Reload to try again.'
          : err instanceof Error
            ? err.message
            : 'An unexpected error occurred.'}
      </p>
      <div className="mt-6 flex justify-center gap-3">
        <button type="button" className="inline-flex min-h-11 items-center rounded-md border border-line-strong px-4 text-sm font-medium" onClick={() => window.location.reload()}>
          Reload
        </button>
        <ButtonLink to="/">Go to overview</ButtonLink>
      </div>
    </div>
  )
}
