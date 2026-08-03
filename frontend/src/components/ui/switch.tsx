import * as RadixSwitch from '@radix-ui/react-switch'

import { cn } from '@/lib/utils'

// Switch — a boolean that applies immediately (Issue 385).
//
// Radix renders <button role="switch" aria-checked>, which is what a native
// checkbox styled as a toggle was only pretending to be. Existing
// getByRole('switch', { name }) queries keep working.
//
// Use Switch for a setting that takes effect on toggle; use Checkbox for a value
// that is submitted or agreed to (see checkbox.tsx).
export interface SwitchProps {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  disabled?: boolean
  id?: string
  'aria-label'?: string
  className?: string
}

export function Switch({
  checked,
  onCheckedChange,
  disabled,
  id,
  className,
  'aria-label': ariaLabel,
}: SwitchProps) {
  return (
    <RadixSwitch.Root
      id={id}
      checked={checked}
      onCheckedChange={onCheckedChange}
      disabled={disabled}
      aria-label={ariaLabel}
      className={cn(
        'inline-flex h-5 w-9 shrink-0 items-center rounded-full border border-strong transition-colors duration-fast ease-standard focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-border focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:pointer-events-none disabled:opacity-50',
        'data-[state=checked]:border-accent data-[state=checked]:bg-accent data-[state=unchecked]:bg-elevated',
        className,
      )}
    >
      <RadixSwitch.Thumb className="pointer-events-none block size-4 translate-x-0.5 rounded-full bg-fg shadow-sm transition-transform duration-fast ease-standard data-[state=checked]:translate-x-[18px] data-[state=checked]:bg-on-accent" />
    </RadixSwitch.Root>
  )
}
