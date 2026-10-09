import { createBrowserRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { AppLoading, Layout, RouteError, ViewError } from './components/layout/Layout'
import Overview from './views/Overview'

// Each view has its own error boundary, so a crash in one view keeps the header, navigation and footer.
const view = { ErrorBoundary: ViewError }

// The overview (and its SVG hero) ships in the entry chunk for a fast first paint;
// the workspace views are code-split and fetched on navigation.
const router = createBrowserRouter([
  {
    path: '/',
    Component: Layout,
    ErrorBoundary: RouteError,
    HydrateFallback: AppLoading,
    children: [
      { index: true, Component: Overview, ...view },
      { path: 'lab', ...view, lazy: () => import('./views/lab/LabView').then((m) => ({ Component: m.default })) },
      { path: 'runs/:runId', ...view, lazy: () => import('./views/run/RunView').then((m) => ({ Component: m.default })) },
      { path: 'experiments', ...view, lazy: () => import('./views/Experiments').then((m) => ({ Component: m.default })) },
      { path: 'history', ...view, lazy: () => import('./views/History').then((m) => ({ Component: m.default })) },
      { path: '*', ...view, lazy: () => import('./views/NotFound').then((m) => ({ Component: m.default })) },
    ],
  },
])

export function App() {
  return <RouterProvider router={router} />
}
