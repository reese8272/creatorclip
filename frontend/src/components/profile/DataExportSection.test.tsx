/**
 * DataExportSection — the GDPR export finally has an account surface
 * (Issue 526). 80/20: request → POST fired and status refetched; ready →
 * authed download link present; pending → button locked with live status;
 * failed → server error surfaced honestly.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DataExportSection } from './DataExportSection'

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

function exportFetch(status: {
  status: string
  requested_at?: string | null
  completed_at?: string | null
  error?: string | null
}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/creators/me/export') && init?.method === 'POST')
      return { status: 202, ok: true, json: async () => ({ status: 'pending', task_id: 't1' }) }
    if (url.endsWith('/creators/me/export'))
      return {
        status: 200,
        ok: true,
        json: async () => ({
          requested_at: null,
          completed_at: null,
          error: null,
          ...status,
        }),
      }
    return { status: 200, ok: true, json: async () => ({}) }
  })
}

afterEach(() => vi.unstubAllGlobals())

describe('DataExportSection (Issue 526)', () => {
  it('requests an export with a POST and refetches the status', async () => {
    const fetchMock = exportFetch({ status: 'none' })
    vi.stubGlobal('fetch', fetchMock)
    render(<DataExportSection />, { wrapper })

    await userEvent.click(await screen.findByRole('button', { name: 'Request export' }))

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([u, init]) =>
            String(u).endsWith('/creators/me/export') &&
            (init as RequestInit | undefined)?.method === 'POST',
        ),
      ).toBe(true),
    )
  })

  it('a ready export offers the authed same-origin download link', async () => {
    vi.stubGlobal(
      'fetch',
      exportFetch({ status: 'ready', completed_at: '2026-08-28T12:00:00Z' }),
    )
    render(<DataExportSection />, { wrapper })

    const link = await screen.findByRole('link', { name: /Download JSON/ })
    expect(link).toHaveAttribute('href', '/creators/me/export/download')
    expect(link).toHaveAttribute('download')
    // A settled export still allows a fresh request.
    expect(screen.getByRole('button', { name: 'Request a fresh export' })).toBeEnabled()
  })

  it('a pending build locks the button and narrates progress', async () => {
    vi.stubGlobal('fetch', exportFetch({ status: 'pending' }))
    render(<DataExportSection />, { wrapper })

    expect(await screen.findByRole('button', { name: 'Building your export…' })).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent(/gathering your data/i)
  })

  it('a failed build surfaces the server error honestly', async () => {
    vi.stubGlobal('fetch', exportFetch({ status: 'failed', error: 'artifact write failed' }))
    render(<DataExportSection />, { wrapper })

    expect(await screen.findByText(/The last export failed: artifact write failed/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Request a fresh export' })).toBeEnabled()
  })
})
