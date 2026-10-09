import { createBrowserRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { AppLoading, Layout, RouteError, ViewError } from './components/layout/Layout'
import { PublicLayout } from './components/layout/PublicLayout'
import Landing from './views/Landing'

// Each view has its own error boundary, so a crash in one view keeps the header, navigation and footer.
const view = { ErrorBoundary: ViewError }

// Two shells: the public landing page (top bar, no sidebar) and the app (sidebar, breadcrumb, guided steps).
// The landing page ships in the entry chunk for a fast first paint; the app views are code-split and fetched on navigation.
const router = createBrowserRouter([
  {
    path: '/',
    ErrorBoundary: RouteError,
    HydrateFallback: AppLoading,
    children: [
      { Component: PublicLayout, children: [{ index: true, Component: Landing, ...view }] },
      {
        Component: Layout,
        children: [
          { path: 'dashboard', ...view, lazy: () => import('./views/Dashboard').then((m) => ({ Component: m.default })) },
          { path: 'lab', ...view, lazy: () => import('./views/lab/LabView').then((m) => ({ Component: m.default })) },
          { path: 'runs/:runId', ...view, lazy: () => import('./views/run/RunView').then((m) => ({ Component: m.default })) },
          { path: 'experiments', ...view, lazy: () => import('./views/Experiments').then((m) => ({ Component: m.default })) },
          { path: 'history', ...view, lazy: () => import('./views/History').then((m) => ({ Component: m.default })) },
          { path: 'guardrails', ...view, lazy: () => import('./views/Guardrails').then((m) => ({ Component: m.default })) },
          { path: '*', ...view, lazy: () => import('./views/NotFound').then((m) => ({ Component: m.default })) },
        ],
      },
    ],
  },
])

export function App() {
  return <RouterProvider router={router} />
}
