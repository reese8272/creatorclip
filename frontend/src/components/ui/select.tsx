import * as RadixSelect from '@radix-ui/react-select'
import type { VariantProps } from 'class-variance-authority'

import { cn } from '@/lib/utils'
import { Check, ChevronDown } from '@/components/ui/icon'
import { ICON_SIZE } from '@/components/ui/iconSizes'
import { selectTriggerVariants } from './selectVariants'

// Select — replaces every native <select> in the SPA (Issue 385).
//
// A native <select> renders with OPERATING-SYSTEM chrome that ignores the design
// system entirely: OS-blue highlights and system fonts punched through an OKLCH
// hue-285 palette. Radix ships unstyled and follows the WAI-ARIA ListBox pattern,
// so the accessibility work — focus management, typeahead, keyboard navigation —
// composes with our tokens instead of fighting them.
//
// DATA-DRIVEN, not compound. shadcn re-exports eight parts; all eight of our call
// sites are flat lists built from a const array or literal <option>s, so a single
// `options` prop collapses 6-10 lines per site to one and lets `selectCls`-style
// ad-hoc class strings be deleted outright.

// Radix Select THROWS on an empty-string value — it reserves '' internally to mean
// "clear the selection". Five of our eight call sites use <option value=""> as
// their default ("Auto", "None", "Inherit"), so this component owns the mapping
// and call sites keep passing '' exactly as they do today. Pushing this to call
// sites would guarantee missing one of the five.
const EMPTY_SENTINEL = '__none__'

const toRadix = (value: string): string => (value === '' ? EMPTY_SENTINEL : value)
const fromRadix = (value: string): string => (value === EMPTY_SENTINEL ? '' : value)

export interface SelectOption {
  value: string
  label: string
  disabled?: boolean
}

export interface SelectProps extends VariantProps<typeof selectTriggerVariants> {
  value: string
  onValueChange: (value: string) => void
  options: SelectOption[]
  /** Shown when `value` matches no option. Distinct from an empty-string option. */
  placeholder?: string
  disabled?: boolean
  id?: string
  'aria-label'?: string
  className?: string
}

export function Select({
  value,
  onValueChange,
  options,
  placeholder,
  disabled,
  id,
  size,
  className,
  'aria-label': ariaLabel,
}: SelectProps) {
  return (
    <RadixSelect.Root
      value={toRadix(value)}
      onValueChange={(next) => onValueChange(fromRadix(next))}
      disabled={disabled}
    >
      <RadixSelect.Trigger
        id={id}
        aria-label={ariaLabel}
        className={cn(selectTriggerVariants({ size }), className)}
      >
        <RadixSelect.Value placeholder={placeholder} />
        <RadixSelect.Icon asChild>
          <ChevronDown className={`${ICON_SIZE.sm} shrink-0 text-muted`} aria-hidden="true" />
        </RadixSelect.Icon>
      </RadixSelect.Trigger>

      <RadixSelect.Portal>
        {/* bg-elevated: an open menu floats above the panel it sits on (L2 in the
            docs/UI.md elevation ladder), so it must not tie with bg-surface. */}
        <RadixSelect.Content
          position="popper"
          sideOffset={4}
          className="z-50 max-h-[var(--radix-select-content-available-height)] min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-md border border-strong bg-elevated shadow-md animate-fade-in"
        >
          <RadixSelect.Viewport className="p-1">
            {options.map((option) => (
              <RadixSelect.Item
                key={option.value}
                value={toRadix(option.value)}
                disabled={option.disabled}
                className="relative flex cursor-default select-none items-center gap-2 rounded-xs py-1.5 pl-7 pr-2 text-body text-fg outline-none data-[disabled]:pointer-events-none data-[highlighted]:bg-accent-soft data-[disabled]:opacity-50 data-[highlighted]:text-accent-text"
              >
                <RadixSelect.ItemIndicator className="absolute left-2 inline-flex items-center">
                  <Check className={ICON_SIZE.sm} aria-hidden="true" />
                </RadixSelect.ItemIndicator>
                <RadixSelect.ItemText>{option.label}</RadixSelect.ItemText>
              </RadixSelect.Item>
            ))}
          </RadixSelect.Viewport>
        </RadixSelect.Content>
      </RadixSelect.Portal>
    </RadixSelect.Root>
  )
}
