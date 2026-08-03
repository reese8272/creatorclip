import * as RadixRadioGroup from '@radix-ui/react-radio-group'

import { cn } from '@/lib/utils'

// RadioGroup — one-of-N (Issue 385).
//
// Compound rather than data-driven (unlike Select): its only call site wraps each
// option in a styled card with a two-line label, so the caller must own the
// option's markup. Radix supplies roving tabindex and arrow-key navigation, which
// is the part a hand-rolled version reliably gets wrong.
//
// NOTE: this primitive is not on Issue 385's original list — Slider,
// DropdownMenu and Popover were, and all three have zero call sites today. It was
// swapped in because it is the last remaining native form control rendering OS
// chrome once Select/Switch/Checkbox land. See docs/DECISIONS.md 2026-08-03.
export interface RadioGroupProps {
  value: string
  onValueChange: (value: string) => void
  children: React.ReactNode
  disabled?: boolean
  'aria-label'?: string
  className?: string
}

export function RadioGroup({
  value,
  onValueChange,
  children,
  disabled,
  className,
  'aria-label': ariaLabel,
}: RadioGroupProps) {
  return (
    <RadixRadioGroup.Root
      value={value}
      onValueChange={onValueChange}
      disabled={disabled}
      aria-label={ariaLabel}
      className={cn('flex flex-col gap-2', className)}
    >
      {children}
    </RadixRadioGroup.Root>
  )
}

export interface RadioGroupItemProps {
  value: string
  id?: string
  disabled?: boolean
  'aria-label'?: string
  className?: string
}

export function RadioGroupItem({
  value,
  id,
  disabled,
  className,
  'aria-label': ariaLabel,
}: RadioGroupItemProps) {
  return (
    <RadixRadioGroup.Item
      value={value}
      id={id}
      disabled={disabled}
      aria-label={ariaLabel}
      className={cn(
        'inline-flex size-4 shrink-0 items-center justify-center rounded-full border border-strong bg-bg transition-colors duration-fast ease-standard focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-border focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:pointer-events-none disabled:opacity-50',
        'data-[state=checked]:border-accent',
        className,
      )}
    >
      <RadixRadioGroup.Indicator className="block size-2 rounded-full bg-accent" />
    </RadixRadioGroup.Item>
  )
}
