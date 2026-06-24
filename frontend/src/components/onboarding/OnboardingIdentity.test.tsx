import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OnboardingIdentity } from './OnboardingIdentity'

type FetchInit = { method?: string; body?: string }
const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })

function renderIdentity(onSaved: () => void, fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <OnboardingIdentity onSaved={onSaved} />
    </QueryClientProvider>,
  )
}

describe('OnboardingIdentity', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('offers both intake modes, with the quick form selected by default (Issue 96)', () => {
    renderIdentity(
      vi.fn(),
      vi.fn(async () => json({ options: [] })),
    )
    expect(screen.getByRole('tab', { name: 'Quick form' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Chat it out' })).toHaveAttribute(
      'aria-selected',
      'false',
    )
    expect(screen.getByText(/Niche \(pick 1–3\)/)).toBeInTheDocument() // form is showing
  })

  it('chats to a proposal, then confirm writes the identity and unlocks DNA (Issue 96)', async () => {
    const saved: unknown[] = []
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: FetchInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/creators/niches')) return json({ options: [{ id: '20', label: 'Gaming' }] })
      if (url.endsWith('/creators/me/identity/chat')) {
        return json({ reply: 'Got it — confirm below.', proposal: { niches: ['20'], audience_summary: 'Speedrunners' } })
      }
      if (url.endsWith('/creators/me/identity') && method === 'POST') {
        saved.push(JSON.parse(init?.body ?? '{}'))
        return json({ version: 1 })
      }
      return json({})
    })
    const onSaved = vi.fn()
    renderIdentity(onSaved, fetchMock)

    fireEvent.click(screen.getByRole('tab', { name: 'Chat it out' }))
    expect(screen.getByText(/Tell me what your channel is about/i)).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Your message'), {
      target: { value: 'gaming speedruns for beginners' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    // The model's reply + the proposal card (with the niche id resolved to a label).
    await waitFor(() => expect(screen.getByText(/Your profile/i)).toBeInTheDocument())
    expect(screen.getByText(/Gaming/)).toBeInTheDocument()
    expect(screen.getByText(/Speedrunners/)).toBeInTheDocument()

    // Confirm writes via the SAME identity endpoint the wizard uses, then unlocks.
    fireEvent.click(screen.getByRole('button', { name: /Save & continue/i }))
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(saved[0]).toMatchObject({ niches: ['20'], audience_summary: 'Speedrunners' })
  })
})
