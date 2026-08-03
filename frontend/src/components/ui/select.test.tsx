import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { Select, type SelectOption } from './select'

const OPTIONS: SelectOption[] = [
  { value: '', label: 'Auto' },
  { value: 'punch', label: 'Punch in' },
  { value: 'static', label: 'Static' },
]

function Harness({ initial = '' }: { initial?: string }) {
  const [value, setValue] = useState(initial)
  return (
    <>
      <Select value={value} onValueChange={setValue} options={OPTIONS} aria-label="Framing" />
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  )
}

describe('Select', () => {
  it('exposes the trigger as a labelled combobox', () => {
    render(<Harness />)
    expect(screen.getByRole('combobox', { name: 'Framing' })).toBeInTheDocument()
  })

  it('opens and selects with the keyboard', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    const trigger = screen.getByRole('combobox', { name: 'Framing' })
    trigger.focus()
    await user.keyboard('{Enter}')
    await user.click(await screen.findByRole('option', { name: 'Punch in' }))
    expect(screen.getByTestId('value')).toHaveTextContent('"punch"')
  })

  it('reports the chosen value to onValueChange', async () => {
    const onValueChange = vi.fn()
    const user = userEvent.setup()
    render(
      <Select value="punch" onValueChange={onValueChange} options={OPTIONS} aria-label="Framing" />,
    )
    await user.click(screen.getByRole('combobox', { name: 'Framing' }))
    await user.click(await screen.findByRole('option', { name: 'Static' }))
    expect(onValueChange).toHaveBeenCalledWith('static')
  })

  // LOAD-BEARING. Radix Select throws on value="" — it reserves the empty string
  // for "clear the selection" — and five of the eight call sites this primitive
  // replaces use <option value=""> as their default. The component owns the
  // sentinel mapping so those call sites keep passing '' unchanged.
  it('round-trips the empty-string default without throwing', async () => {
    const user = userEvent.setup()
    render(<Harness initial="" />)

    // Renders '' as a real selected option, not as a placeholder.
    expect(screen.getByRole('combobox', { name: 'Framing' })).toHaveTextContent('Auto')

    // And selecting it back reports '' to the caller, not the internal sentinel.
    await user.click(screen.getByRole('combobox', { name: 'Framing' }))
    await user.click(await screen.findByRole('option', { name: 'Punch in' }))
    expect(screen.getByTestId('value')).toHaveTextContent('"punch"')

    await user.click(screen.getByRole('combobox', { name: 'Framing' }))
    await user.click(await screen.findByRole('option', { name: 'Auto' }))
    expect(screen.getByTestId('value')).toHaveTextContent('""')
  })

  it('does not open when disabled', async () => {
    const user = userEvent.setup()
    render(
      <Select value="" onValueChange={vi.fn()} options={OPTIONS} disabled aria-label="Framing" />,
    )
    await user.click(screen.getByRole('combobox', { name: 'Framing' }))
    expect(screen.queryByRole('option')).toBeNull()
  })
})
