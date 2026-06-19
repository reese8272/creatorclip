import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AccountDeletion } from './AccountDeletion'

describe('AccountDeletion', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  beforeEach(() => {
    fetchMock = vi.fn(async () => ({ ok: true, status: 204 }))
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('requires a two-step confirm before calling DELETE /auth/me', async () => {
    const user = userEvent.setup()
    render(<AccountDeletion />)

    // First click only arms the confirm — no destructive call yet.
    await user.click(screen.getByRole('button', { name: 'Delete my account' }))
    expect(fetchMock).not.toHaveBeenCalled()

    // Cancel returns to the idle state.
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('button', { name: 'Delete my account' })).toBeInTheDocument()

    // Confirming fires the DELETE.
    await user.click(screen.getByRole('button', { name: 'Delete my account' }))
    await user.click(screen.getByRole('button', { name: 'Yes, permanently delete' }))
    expect(fetchMock).toHaveBeenCalledWith(
      '/auth/me',
      expect.objectContaining({ method: 'DELETE' }),
    )
  })
})
