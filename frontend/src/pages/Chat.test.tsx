import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { Chat } from './Chat'

// jsdom doesn't implement Element.scrollTo (the auto-scroll-to-latest effect).
beforeAll(() => {
  Element.prototype.scrollTo = vi.fn()
})

afterEach(() => vi.unstubAllGlobals())

describe('Chat', () => {
  it('shows clickable suggestion pills in the empty state', () => {
    vi.stubGlobal('fetch', vi.fn())
    render(<Chat />)
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
    render(<Chat />)
    await userEvent.click(screen.getByRole('button', { name: 'When should I post?' }))
    // Optimistic user bubble appears; the empty-state pills are gone.
    expect(await screen.findByText('When should I post?')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'What were my best videos this month?' })).toBeNull()
  })
})
