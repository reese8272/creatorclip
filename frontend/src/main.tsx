import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
// Self-hosted variable fonts (Fontsource) — replaces the Google Fonts @import
// so no visitor IP ever reaches fonts.googleapis.com (GDPR: LG München
// 3 O 17493/20). Variable packages are required: the type scale uses weight
// 650, which only a continuous wght axis serves.
import '@fontsource-variable/geist'
import '@fontsource-variable/geist-mono'
import '@fontsource-variable/inter'
import './index.css'
import App from './App.tsx'
import { queryClient } from '@/lib/queryClient'

createRoot(document.getElementById('root')!, {
  onUncaughtError: (error, errorInfo) => {
    console.error('[React] uncaught error', error, errorInfo)
  },
  onRecoverableError: (error, errorInfo) => {
    console.error('[React] recoverable error', error, errorInfo)
  },
}).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
