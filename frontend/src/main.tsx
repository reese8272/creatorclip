import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
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
