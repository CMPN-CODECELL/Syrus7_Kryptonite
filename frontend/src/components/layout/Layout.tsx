import { useEffect, useRef, useState, type ComponentType } from 'react'
import { Link, NavLink, Outlet, ScrollRestoration, isRouteErrorResponse, useLocation, useNavigate, useNavigation, useRouteError } from 'react-router'
import { DISCLAIMER } from '../../api/contract.gen'
import { useDataStatus, useHealth, useOnline } from '../../api/hooks'
import { formatDate } from '../../lib/format'
import {
  Badge,
  ButtonLink,
  ErrorState,
  IconAlert,
  IconArrowRight,
  IconCalendar,
  IconCheck,
  IconClock,
  IconDatabase,
  IconGrid,
  IconHelp,
  IconHome,
  IconLayers,
  IconMenu,
  IconShield,
  IconSpark,
  IconWifiOff,
  IconX,
  LoadingBlock,
  Notice,
} from '../ui'
import { pageName, useDocumentTitle } from './title'

interface NavEntry {
  to: string
  label: string
  icon: ComponentType<{ size?: number; className?: string }>
  end?: boolean
}

const NAV: Array<{ title: string; items: NavEntry[] }> = [
  {
    title: 'Workspace',
    items: [
      { to: '/dashboard', label: 'Dashboard', icon: IconGrid, end: true },
      { to: '/lab', label: 'Portfolio Lab', icon: IconSpark },
      { to: '/experiments', label: 'Experiments', icon: IconLayers },
      { to: '/history', label: 'History', icon: IconClock },
    ],
  },
  {
    title: 'Trust and help',
    items: [
      { to: '/guardrails', label: 'Guardrails', icon: IconShield },
      { to: '/#how', label: 'How it works', icon: IconHelp },
      { to: '/', label: 'Home page', icon: IconHome, end: true },
    ],
  },
]

export function Brand({ labelClassName = '' }: { labelClassName?: string }) {
  return (
    <Link to="/" className="flex min-h-11 items-center gap-2.5 rounded-md pr-2 text-ink" aria-label="QuantFolio home">
      <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden="true" focusable="false" className="shrink-0">
        <rect x="1" y="1" width="30" height="30" rx="8" fill="#1f48d6" />
        <ellipse cx="16" cy="16" rx="11" ry="4.6" fill="none" stroke="#fff" strokeOpacity=".55" strokeWidth="1.3" transform="rotate(-24 16 16)" />
        <rect x="11.5" y="11.5" width="9" height="9" rx="2" fill="#fff" transform="rotate(45 16 16)" />
        <circle cx="25.2" cy="11.6" r="2" fill="#5ed1c9" />
      </svg>
      <span className={`text-[1.15rem] font-semibold tracking-tight whitespace-nowrap ${labelClassName}`}>QuantFolio</span>
    </Link>
  )
}

/**
 * The menu links. In the rail (`rail` set) the icons keep their place and only the words fade in and out, so
 * opening the rail never moves anything the pointer is on.
 */
function NavItems({ rail }: { rail?: { open: boolean; fade: string } }) {
  return (
    <div className="flex flex-col gap-5">
      {NAV.map((group) => (
        <div key={group.title}>
          <p className="relative flex h-6 items-center px-3 text-[0.7rem] font-semibold tracking-[0.08em] text-faint uppercase">
            <span className={`whitespace-nowrap ${rail?.fade ?? ''}`}>{group.title}</span>
            {rail ? (
              <span
                className={`absolute top-1/2 left-[11px] h-px w-[18px] bg-line-strong transition-opacity duration-150 ${rail.open ? 'opacity-0' : 'opacity-100'}`}
                aria-hidden="true"
              />
            ) : null}
          </p>
          <ul className="flex flex-col gap-0.5">
            {group.items.map(({ to, label, icon: Icon, end }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  end={end}
                  className={({ isActive }) =>
                    `relative flex min-h-11 items-center gap-3 rounded-md text-sm font-medium transition-colors pointer-fine:min-h-10 ${rail ? 'px-[11px]' : 'px-3'} ${
                      isActive
                        ? 'bg-cobalt-50 text-cobalt-800 before:absolute before:inset-y-2 before:-left-3 before:w-[3px] before:rounded-r-full before:bg-cobalt'
                        : 'text-ink-2 hover:bg-mist hover:text-ink'
                    }`
                  }
                >
                  {({ isActive }) => (
                    <>
                      <Icon size={18} className={`shrink-0 ${isActive ? 'text-cobalt' : 'text-muted'}`} />
                      <span className={`whitespace-nowrap ${rail?.fade ?? ''}`}>{label}</span>
                    </>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

/** The API state as a single dot, for the closed rail. */
function ApiDot() {
  const health = useHealth()
  const online = useOnline()
  const tone = !online ? 'bg-amber' : health.isError ? 'bg-danger' : health.isPending ? 'bg-line-strong' : 'qf-pulse-dot bg-teal'
  return (
    <span className="inline-flex h-10 w-10 items-center justify-center rounded-full bg-mist" aria-hidden="true">
      <span className={`h-2 w-2 rounded-full ${tone}`} />
    </span>
  )
}

/**
 * The wide-screen menu: a slim rail of icons that opens over the page while the mouse is on it or keyboard focus
 * is inside it, and closes again when either leaves. The page itself never moves.
 */
function SidebarRail() {
  const [open, setOpen] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  // A short delay on the way in, so the rail does not flash open when the pointer only crosses it.
  const later = (next: boolean, ms: number) => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setOpen(next), ms)
  }
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const fade = `transition-opacity duration-150 ${open ? 'opacity-100 delay-75' : 'opacity-0'}`

  return (
    <aside
      aria-label="Sidebar"
      data-testid="sidebar"
      data-open={open ? 'true' : 'false'}
      onPointerEnter={(e) => {
        if (e.pointerType === 'mouse') later(true, 90)
      }}
      onPointerLeave={(e) => {
        if (e.pointerType === 'mouse') later(false, 140)
      }}
      onFocus={(e) => {
        try {
          if (e.target.matches(':focus-visible')) later(true, 0)
        } catch {
          /* :focus-visible unsupported: leave the rail as it is */
        }
      }}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) later(false, 0)
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') later(false, 0)
      }}
      className={`fixed inset-y-0 left-0 z-40 hidden flex-col overflow-hidden border-r border-line bg-canvas transition-[width,box-shadow] duration-200 ease-out lg:flex ${
        open ? 'w-60 shadow-float' : 'w-16'
      }`}
    >
      <div className="flex h-16 shrink-0 items-center px-[18px]">
        <Brand labelClassName={fade} />
      </div>
      <nav aria-label="Main" className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto px-3 pt-3 pb-4">
        <NavItems rail={{ open, fade }} />
      </nav>
      <div className="relative shrink-0 p-3">
        <div className={`w-[13.5rem] ${fade}`}>
          <SidebarStatus />
        </div>
        <span className={`absolute bottom-3 left-3 transition-opacity duration-150 ${open ? 'opacity-0' : 'opacity-100'}`}>
          <ApiDot />
        </span>
      </div>
    </aside>
  )
}

export function ApiBadge() {
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

/** The data source and its last close, as a small card at the foot of the sidebar. */
function SidebarStatus() {
  const ds = useDataStatus()
  const a = ds.data?.active
  const warn = !!a && (a.stale || a.label === 'synthetic test')
  return (
    <div className="rounded-lg border border-line bg-mist px-3 py-2.5 text-xs leading-relaxed">
      <ApiBadge />
      {a ? (
        <p className={`mt-2 flex items-start gap-1.5 ${warn ? 'text-amber' : 'text-muted'}`} title={`${a.source}; ${a.first_date} to ${a.last_date}`}>
          {warn ? <IconAlert size={13} className="mt-0.5 shrink-0" /> : <IconDatabase size={13} className="mt-0.5 shrink-0" />}
          <span>
            {a.n_symbols} stocks, {a.label}
            {a.stale ? ' (stale)' : ''}. Last close {formatDate(a.last_date)}.
          </span>
        </p>
      ) : null}
    </div>
  )
}

/** Small chips on the right of the top bar: the market and the as-of date of the prices. */
function DataChips() {
  const ds = useDataStatus()
  const a = ds.data?.active
  if (!a) return null
  const warn = a.stale || a.label === 'synthetic test'
  return (
    <>
      <span className={`hidden rounded-md px-2 py-1 text-[0.7rem] font-semibold tracking-[0.06em] uppercase xl:inline-flex ${warn ? 'bg-amber-50 text-amber' : 'bg-mist-2 text-muted'}`}>
        {warn ? (a.stale ? 'Stale data' : 'Synthetic data') : `NIFTY 50 · ${a.label}`}
      </span>
      <span className="inline-flex items-center gap-1.5 rounded-md border border-line bg-canvas px-2.5 py-1 text-xs text-ink-2" title={`${a.source}; ${a.first_date} to ${a.last_date}`}>
        <IconCalendar size={14} className="text-muted" />
        As of {formatDate(a.last_date)}
      </span>
    </>
  )
}

/** The app's content width; the public landing page keeps a narrower column. */
const APP_WIDTH = 'max-w-[1440px] px-4 sm:px-6 lg:px-8'
const PUBLIC_WIDTH = 'max-w-[1320px] px-4 sm:px-6'

export function Banners({ wide = false }: { wide?: boolean }) {
  const online = useOnline()
  const health = useHealth()
  const ds = useDataStatus()
  const active = ds.data?.active
  return (
    <div className={`mx-auto flex w-full flex-col gap-2 empty:hidden ${wide ? APP_WIDTH : PUBLIC_WIDTH}`} aria-live="polite">
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

export function NavigationProgress() {
  const nav = useNavigation()
  if (nav.state === 'idle') return null
  return (
    <div className="fixed inset-x-0 top-0 z-50 h-0.5 overflow-hidden bg-cobalt-100" role="progressbar" aria-label="Loading page">
      <div className="qf-indeterminate h-full w-1/3 bg-cobalt" />
    </div>
  )
}

const GUIDE = [
  { to: '/dashboard', label: 'Start', match: /^\/dashboard/ },
  { to: '/lab', label: 'Build and check', match: /^\/lab/ },
  { to: '/history', label: 'Run and read the result', match: /^\/runs\// },
]

const NEXT: Record<number, { to: string; label: string }> = {
  0: { to: '/lab', label: 'Next: Build a portfolio' },
  1: { to: '#preflight', label: 'Next: Check and run' },
  2: { to: '/experiments', label: 'Next: Compare settings' },
}

/** The guided path for a first visit, shown on the three pages a first run passes through. */
function GuidedPath({ path }: { path: string }) {
  const at = GUIDE.findIndex((g) => g.match.test(path))
  if (at < 0) return null
  const next = NEXT[at]
  return (
    <div className="border-b border-line bg-canvas" data-testid="guided-path">
      <div className={`mx-auto flex items-center gap-3 py-2.5 ${APP_WIDTH}`}>
        <p className="hidden shrink-0 text-[0.7rem] font-semibold tracking-[0.08em] text-cobalt uppercase md:block">Guided path</p>
        <ol className="flex min-w-0 items-center gap-1.5 overflow-x-auto" aria-label="Guided path">
          {GUIDE.map((g, i) => {
            const state = i < at ? 'done' : i === at ? 'current' : 'todo'
            return (
              <li key={g.label} className="flex shrink-0 items-center gap-1.5">
                {i > 0 ? <span className={`h-px w-4 sm:w-6 ${i <= at ? 'bg-cobalt-200' : 'bg-line-strong'}`} aria-hidden="true" /> : null}
                <Link
                  to={g.to}
                  aria-current={state === 'current' ? 'step' : undefined}
                  className={`inline-flex min-h-9 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium transition-colors sm:text-sm ${
                    state === 'current'
                      ? 'bg-cobalt text-white shadow-sm'
                      : state === 'done'
                        ? 'border border-teal-100 bg-teal-50 text-teal hover:border-teal-600/40'
                        : 'border border-line text-muted hover:border-line-strong hover:text-ink'
                  }`}
                >
                  <span
                    className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[0.7rem] font-semibold tabular ${
                      state === 'current' ? 'bg-white/20' : state === 'done' ? 'bg-teal-100' : 'bg-mist-2'
                    }`}
                  >
                    {state === 'done' ? <IconCheck size={12} /> : i + 1}
                  </span>
                  <span className={state === 'current' ? '' : 'hidden sm:inline'}>{g.label}</span>
                </Link>
              </li>
            )
          })}
        </ol>
        {next.to.startsWith('#') ? (
          <a
            href={next.to}
            className="ml-auto hidden min-h-9 shrink-0 items-center gap-1.5 rounded-md bg-ink px-3 text-sm font-medium text-white hover:bg-ink-2 sm:inline-flex"
          >
            {next.label} <IconArrowRight size={14} />
          </a>
        ) : (
          <ButtonLink to={next.to} variant="dark" size="sm" iconRight={<IconArrowRight size={14} />} className="ml-auto hidden shrink-0 sm:inline-flex">
            {next.label}
          </ButtonLink>
        )}
      </div>
    </div>
  )
}

/** The app shell: a slim white menu rail on wide screens, a top bar with the page trail and data date, and a light grey workspace. */
export function Layout() {
  const loc = useLocation()
  useDocumentTitle()
  // The mobile menu belongs to the page it was opened on, so navigating closes it.
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const menuOpen = menuFor === loc.pathname

  return (
    <div className="min-h-dvh bg-mist lg:pl-16">
      <a href="#main" className="sr-only-focusable fixed top-2 left-2 z-50 rounded-md bg-ink px-3 py-2 text-sm text-white">
        Skip to content
      </a>
      <NavigationProgress />
      <SidebarRail />
      <header className="sticky top-0 z-30 border-b border-line bg-canvas/92 backdrop-blur supports-[backdrop-filter]:bg-canvas/85">
        <div className={`mx-auto flex h-16 items-center gap-3 ${APP_WIDTH}`}>
          <div className="lg:hidden">
            <Brand />
          </div>
          <p className="hidden min-w-0 items-center gap-2 text-sm lg:flex">
            <Link to="/" className="text-muted hover:text-ink">
              QuantFolio
            </Link>
            <span className="text-line-strong" aria-hidden="true">
              /
            </span>
            <span className="truncate font-semibold text-ink">{pageName(loc.pathname)}</span>
          </p>
          <div className="ml-auto flex min-w-0 items-center gap-2">
            <span className="hidden items-center gap-2 sm:flex">
              <DataChips />
            </span>
            <span className="lg:hidden">
              <ApiBadge />
            </span>
            <button
              type="button"
              className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-md text-ink-2 hover:bg-mist-2 lg:hidden"
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
          <nav id="mobile-nav" aria-label="Main" className="max-h-[calc(100dvh-4rem)] overflow-y-auto border-t border-line bg-canvas px-4 pt-3 pb-4 lg:hidden">
            <NavItems />
            <div className="mt-4">
              <SidebarStatus />
            </div>
          </nav>
        ) : null}
      </header>
      <GuidedPath path={loc.pathname} />
      <div className="flex min-h-dvh flex-col">
        <div className="pt-3 empty:pt-0">
          <Banners wide />
        </div>
        <main id="main" tabIndex={-1} className="min-h-dvh flex-1 outline-none">
          <Outlet />
        </main>
        <Footer wide />
      </div>
      <ScrollRestoration />
    </div>
  )
}

export function Footer({ wide = false }: { wide?: boolean }) {
  const health = useHealth()
  return (
    <footer className="mt-16 border-t border-line bg-canvas">
      <div className={`mx-auto flex flex-col gap-3 py-8 text-sm text-muted md:flex-row md:items-start md:justify-between ${wide ? APP_WIDTH : PUBLIC_WIDTH}`}>
        <div className="max-w-prose">
          <p className="font-semibold text-ink-2">{DISCLAIMER}</p>
          <p className="mt-1 leading-relaxed">
            QuantFolio compares QAOA circuits run on a simulator with classical solvers on the same constrained problem. Historical
            estimates do not predict future returns. Universe membership is today&apos;s NIFTY 50, so past results carry survivorship
            bias.
          </p>
        </div>
        <p className="shrink-0 text-xs tabular">
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
    <div className={`mx-auto py-10 ${APP_WIDTH}`} data-testid="view-error">
      <ErrorState title={chunk ? 'This page could not be loaded' : 'This view stopped working'} onRetry={retry}>
        {chunk
          ? 'Part of the app failed to download, usually because the connection dropped or the app was updated.'
          : 'Something unexpected happened while showing this page. The rest of the app still works; try again or go back to the dashboard.'}
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
      <h1 className="mt-2 text-3xl font-semibold tracking-tight text-ink">{notFound ? 'Page not found' : chunk ? 'This page could not be loaded' : 'Something broke on this page'}</h1>
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
        <ButtonLink to="/dashboard">Go to the dashboard</ButtonLink>
      </div>
    </div>
  )
}
