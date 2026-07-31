import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { Chat } from './Chat'

// jsdom doesn't implement Element.scrollTo (the auto-scroll-to-latest effect).
beforeAll(() => {
  Element.prototype.scrollTo = vi.fn()
})

afterEach(() => vi.unstubAllGlobals())

// Chat now carries the AskSurfaceTabs row (Issue 355), which routes — so the
// page needs a router in scope.
const renderChat = () => render(<Chat />, { wrapper: MemoryRouter })

describe('Chat', () => {
  it('shows clickable suggestion pills in the empty state', () => {
    vi.stubGlobal('fetch', vi.fn())
    renderChat()
    expect(screen.getByRole('button', { name: 'When should I post?' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'What were my best videos this month?' })).toBeInTheDocument()
  })

  it('clicking a suggestion sends it as the user message', async () => {
    const json = (body: unknown) => ({ status: 200, ok: true, json: async () => body })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        json({ task_id: 't', stream_url: null, conversation_id: 'c' }),
      ),
    )
    renderChat()
    await userEvent.click(screen.getByRole('button', { name: 'When should I post?' }))
    // Optimistic user bubble appears; the empty-state pills are gone.
    expect(await screen.findByText('When should I post?')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'What were my best videos this month?' })).toBeNull()
  })
})
