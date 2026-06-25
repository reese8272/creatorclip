import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { NotificationPreferencesSection } from './NotificationPreferencesSection'

// Issue 245 — the preferences pane must:
//  * render the transactional toggle locked-on (disabled),
//  * render lifecycle + in-app toggles interactive,
//  * PATCH only the lifecycle field when it is toggled (never transactional).

const PREFS = {
  email_transactional: true,
  email_lifecycle: true,
  inapp_enabled: true,
  push_enabled: false,
}

describe('NotificationPreferencesSection', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn(async (_url: string, opts?: { method?: string; body?: string }) => {
      if (opts?.method === 'PATCH') {
        const patch = JSON.parse(opts.body ?? '{}')
        return { ok: true, status: 200, json: async () => ({ ...PREFS, ...patch }) }
      }
      return { ok: true, status: 200, json: async () => PREFS }
    })
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('renders transactional locked-on and lifecycle interactive', async () => {
    render(<NotificationPreferencesSection />)

    const transactional = await screen.findByRole('switch', { name: /transactional emails/i })
    expect(transactional).toBeChecked()
    expect(transactional).toBeDisabled()

    const lifecycle = screen.getByRole('switch', { name: /lifecycle emails/i })
    expect(lifecycle).toBeEnabled()
  })

  it('PATCHes only the lifecycle field when toggled off', async () => {
    const user = userEvent.setup()
    render(<NotificationPreferencesSection />)

    const lifecycle = await screen.findByRole('switch', { name: /lifecycle emails/i })
    await user.click(lifecycle)

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        (c) => (c[1] as { method?: string } | undefined)?.method === 'PATCH',
      )
      expect(patchCall).toBeTruthy()
      const body = JSON.parse((patchCall![1] as { body: string }).body)
      // Only the lifecycle field is sent — transactional is never in the body.
      expect(body).toEqual({ email_lifecycle: false })
      expect('email_transactional' in body).toBe(false)
    })
  })
})
