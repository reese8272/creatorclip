import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { Checkbox } from './checkbox'
import { RadioGroup, RadioGroupItem } from './radio-group'
import { Switch } from './switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from './tabs'
import { Tooltip } from './tooltip'

describe('Switch', () => {
  it('is a switch that reports its state and toggles with Space', async () => {
    const user = userEvent.setup()
    const onCheckedChange = vi.fn()
    render(<Switch checked={false} onCheckedChange={onCheckedChange} aria-label="Captions" />)

    const control = screen.getByRole('switch', { name: 'Captions' })
    expect(control).toHaveAttribute('aria-checked', 'false')

    control.focus()
    await user.keyboard(' ')
    expect(onCheckedChange).toHaveBeenCalledWith(true)
  })

  it('does not fire when disabled', async () => {
    const user = userEvent.setup()
    const onCheckedChange = vi.fn()
    render(
      <Switch checked={false} onCheckedChange={onCheckedChange} disabled aria-label="Captions" />,
    )
    await user.click(screen.getByRole('switch', { name: 'Captions' }))
    expect(onCheckedChange).not.toHaveBeenCalled()
  })
})

describe('Checkbox', () => {
  it('keeps getByRole("checkbox") + toBeChecked working across the Radix swap', async () => {
    const user = userEvent.setup()
    function Harness() {
      const [checked, setChecked] = useState(false)
      return <Checkbox checked={checked} onCheckedChange={setChecked} aria-label="I agree" />
    }
    render(<Harness />)

    const box = screen.getByRole('checkbox', { name: 'I agree' })
    expect(box).not.toBeChecked()
    await user.click(box)
    expect(box).toBeChecked()
  })

  // The sign-in clickwrap depends on this: Radix renders a <button>, and a
  // wrapping <label> without htmlFor silently stops toggling on text click.
  it('toggles when its associated label text is clicked', async () => {
    const user = userEvent.setup()
    function Harness() {
      const [checked, setChecked] = useState(false)
      return (
        <>
          <Checkbox id="tos" checked={checked} onCheckedChange={setChecked} />
          <label htmlFor="tos">I agree to the Terms</label>
        </>
      )
    }
    render(<Harness />)

    await user.click(screen.getByText('I agree to the Terms'))
    expect(screen.getByRole('checkbox')).toBeChecked()
  })
})

describe('RadioGroup', () => {
  it('selects one of N and moves with arrow keys', async () => {
    const user = userEvent.setup()
    function Harness() {
      const [value, setValue] = useState('fast')
      return (
        <RadioGroup value={value} onValueChange={setValue} aria-label="Analysis mode">
          <RadioGroupItem value="fast" aria-label="Fast" />
          <RadioGroupItem value="deep" aria-label="Deep" />
        </RadioGroup>
      )
    }
    render(<Harness />)

    expect(screen.getByRole('radio', { name: 'Fast' })).toBeChecked()
    await user.click(screen.getByRole('radio', { name: 'Fast' }))

    // `{ArrowDown>}` HOLDS the key (no keyup) deliberately. Radix's roving-focus
    // group defers the move with `setTimeout(() => focusFirst(...))`, while
    // RadioGroup only selects on focus if its document-level keydown flag is
    // still set — and that flag is cleared on keyup. A real key press lasts long
    // enough for the timer to land in between; userEvent's default press fires
    // keydown and keyup back-to-back with no macrotask gap, so selection would
    // never happen. This is a harness artifact, not a component defect.
    await user.keyboard('{ArrowDown>}')
    await waitFor(() => expect(screen.getByRole('radio', { name: 'Deep' })).toHaveFocus())
    expect(screen.getByRole('radio', { name: 'Deep' })).toBeChecked()
  })
})

describe('Tabs', () => {
  // The hand-rolled tablist this replaces had role="tab" but no tabpanel and no
  // aria-controls — it announced itself as tabs while providing none of it.
  it('wires triggers to panels with the full ARIA tabs contract', async () => {
    const user = userEvent.setup()
    render(
      <Tabs defaultValue="short">
        <TabsList aria-label="Editor mode">
          <TabsTrigger value="short">Short-form clip</TabsTrigger>
          <TabsTrigger value="long">Long-form source</TabsTrigger>
        </TabsList>
        <TabsContent value="short">Short panel</TabsContent>
        <TabsContent value="long">Long panel</TabsContent>
      </Tabs>,
    )

    const shortTab = screen.getByRole('tab', { name: 'Short-form clip' })
    expect(shortTab).toHaveAttribute('aria-selected', 'true')
    expect(shortTab).toHaveAttribute('aria-controls')
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Short panel')

    await user.click(screen.getByRole('tab', { name: 'Long-form source' }))
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Long panel')
  })
})

describe('Tooltip', () => {
  it('describes its trigger on focus without replacing the accessible name', async () => {
    const user = userEvent.setup()
    render(
      <Tooltip content="Step forward one frame">
        <button>Step</button>
      </Tooltip>,
    )

    const trigger = screen.getByRole('button', { name: 'Step' })
    await user.tab()
    expect(trigger).toHaveFocus()
    expect(await screen.findByText('Step forward one frame')).toBeInTheDocument()
    // aria-describedby, never aria-label — the trigger keeps its own name.
    expect(trigger).toHaveAccessibleName('Step')
  })
})
