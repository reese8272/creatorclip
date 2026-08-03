import * as RadixCheckbox from '@radix-ui/react-checkbox'

import { cn } from '@/lib/utils'
import { Check } from '@/components/ui/icon'
import { ICON_SIZE } from '@/components/ui/iconSizes'

// Checkbox — a value that is agreed to or submitted (Issue 385).
//
// This is the primitive behind the sign-in clickwrap, so two things matter more
// here than styling:
//
//   1. Radix renders a <button role="checkbox" aria-checked>, NOT an <input>. A
//      <button> is a labelable element, so `<label htmlFor>` still works — but a
//      wrapping <label> with no htmlFor silently stops toggling on text click.
//      Always pair with an explicit htmlFor/id.
//   2. Existing getByRole('checkbox') queries and jest-dom's toBeChecked() both
//      keep working against the ARIA state, so consent tests need no rewrite.
//
// Use Checkbox for agreement/selection; use Switch for a setting that applies on
// toggle (see switch.tsx).
export interface CheckboxProps {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  disabled?: boolean
  id?: string
  'aria-label'?: string
  className?: string
}

export function Checkbox({
  checked,
  onCheckedChange,
  disabled,
  id,
  className,
  'aria-label': ariaLabel,
}: CheckboxProps) {
  return (
    <RadixCheckbox.Root
      id={id}
      checked={checked}
      onCheckedChange={(next) => onCheckedChange(next === true)}
      disabled={disabled}
      aria-label={ariaLabel}
      className={cn(
        'inline-flex size-4 shrink-0 items-center justify-center rounded-xs border border-strong bg-bg transition-colors duration-fast ease-standard focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-border focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:pointer-events-none disabled:opacity-50',
        'data-[state=checked]:border-accent data-[state=checked]:bg-accent data-[state=checked]:text-on-accent',
        className,
      )}
    >
      <RadixCheckbox.Indicator>
        <Check className={ICON_SIZE.xs} aria-hidden="true" />
      </RadixCheckbox.Indicator>
    </RadixCheckbox.Root>
  )
}
