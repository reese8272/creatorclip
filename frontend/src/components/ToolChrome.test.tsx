import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ToolChrome } from './ToolChrome'
import { ToolShell } from '@/components/layout/ToolShell'

// Drive the chrome through the real auth probe, as AuthGate.test does — ToolChrome
// reads useAuth for the Nav's user/balance.
function renderChrome() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter basename="/app" initialEntries={['/app/editor']}>
        <Routes>
          <Route element={<ToolChrome />}>
            <Route path="editor" element={<ToolShell>WORKSPACE</ToolShell>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => ({ channel_title: 'C' }) }),
  )
})
afterEach(() => vi.unstubAllGlobals())

describe('ToolChrome', () => {
  it('renders the primary nav around the tool page', async () => {
    renderChrome()
    expect(await screen.findByText('WORKSPACE')).toBeInTheDocument()
    expect(screen.getAllByRole('navigation').length).toBeGreaterThan(0)
  })

  // The marketing footer is what AppChrome adds and ToolChrome deliberately does
  // not. Assert by counting the legal-link set: exactly one, coming from the
  // shell's status bar. Two would mean the footer crept back in.
  it('drops the marketing footer — the legal links appear exactly once', async () => {
    renderChrome()
    await screen.findByText('WORKSPACE')
    expect(screen.getAllByRole('link', { name: 'Terms' })).toHaveLength(1)
    expect(screen.getAllByRole('contentinfo')).toHaveLength(1)
  })

  it('engages the viewport-height shell and lifts the fixed activity panel clear of it', async () => {
    renderChrome()
    await screen.findByText('WORKSPACE')
    const chrome = document.querySelector('[data-tool-chrome]')
    expect(chrome?.className).toContain('lg:h-dvh')
    expect(chrome?.className).toContain('lg:overflow-hidden')
    // Below lg the shell must disengage so mobile keeps a stacked, scrolling page.
    expect(chrome?.className).toContain('min-h-dvh')
    expect(chrome?.className).toContain('lg:[--app-bottom-inset:2.25rem]')
  })
})
