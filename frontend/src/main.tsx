import '@fontsource-variable/plus-jakarta-sans/index.css'
import './styles.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App } from './App'
import { shouldRetry } from './api/hooks'
import { LabProvider } from './state/lab'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: shouldRetry, refetchOnWindowFocus: false, staleTime: 15_000 },
    mutations: { retry: false },
  },
})

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <LabProvider>
        <App />
      </LabProvider>
    </QueryClientProvider>
  </StrictMode>,
)
