/**
 * Modal — the WAI-ARIA dialog contract (newly load-bearing under the review
 * page's SchedulePublishDialog): role/aria-modal/aria-labelledby, initial
 * focus into the panel on open, focus restore on close, Escape-to-close.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { Modal } from './modal'

function Host({ onClose = () => {} }: { onClose?: () => void }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button onClick={() => setOpen(true)}>Open it</button>
      <Modal
        open={open}
        title="My dialog"
        onClose={() => {
          setOpen(false)
          onClose()
        }}
      >
        <p>Body</p>
      </Modal>
    </>
  )
}

describe('Modal', () => {
  it('exposes the dialog contract: role, aria-modal, title-labelled name', async () => {
    render(<Host />)
    await userEvent.click(screen.getByRole('button', { name: 'Open it' }))

    const dialog = screen.getByRole('dialog', { name: 'My dialog' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    // aria-labelledby wired to the actual title element.
    const titleId = dialog.getAttribute('aria-labelledby')
    expect(titleId).toBeTruthy()
    expect(document.getElementById(titleId!)).toHaveTextContent('My dialog')
  })

  it('moves focus into the panel on open and restores it to the opener on close', async () => {
    render(<Host />)
    const opener = screen.getByRole('button', { name: 'Open it' })
    await userEvent.click(opener)

    expect(document.activeElement).toBe(screen.getByRole('dialog'))

    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(document.activeElement).toBe(opener)
  })

  it('closes on Escape', async () => {
    const onClose = vi.fn()
    render(<Host onClose={onClose} />)
    await userEvent.click(screen.getByRole('button', { name: 'Open it' }))

    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
