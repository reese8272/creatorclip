import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthGate } from './AuthGate'

// AuthGate is the load-bearing security boundary for the SPA: anonymous visitors
// must be sent to login, authenticated ones must reach the protected page. We
// drive it through the auth probe (useAuth → /auth/me) via a stubbed fetch.
function renderGate() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={['/app/profile']}>
        <Routes>
          <Route element={<AuthGate />}>
            <Route path="profile" element={<div>PROTECTED</div>} />
          </Route>
          <Route path="login" element={<div>LOGIN</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('AuthGate', () => {
  it('redirects anonymous visitors (401) to login', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ status: 401, ok: false, json: async () => ({ detail: 'no' }) }),
    )
    renderGate()
    expect(await screen.findByText('LOGIN')).toBeInTheDocument()
  })

  it('renders the protected page for an authenticated creator', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => ({ channel_title: 'C' }) }),
    )
    renderGate()
    expect(await screen.findByText('PROTECTED')).toBeInTheDocument()
  })
})
