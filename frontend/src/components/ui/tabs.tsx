import * as RadixTabs from '@radix-ui/react-tabs'

import { cn } from '@/lib/utils'
import { tabsTriggerVariants } from './tabsVariants'

// Tabs — WAI-ARIA tabs (Issue 385).
//
// This replaces a hand-rolled tablist that had role="tablist" and role="tab" but
// NO role="tabpanel", no aria-controls, no id wiring and no roving tabindex — a
// broken authoring pattern that announced itself as tabs to a screen reader while
// providing none of the navigation that promise implies. Radix supplies all of it.
// Declared as a function rather than `export const Tabs = RadixTabs.Root` so
// react-refresh/only-export-components can see it is a component.
export function Tabs({ children, ...props }: React.ComponentProps<typeof RadixTabs.Root>) {
  return <RadixTabs.Root {...props}>{children}</RadixTabs.Root>
}

export function TabsList({
  children,
  className,
  ...props
}: React.ComponentProps<typeof RadixTabs.List>) {
  return (
    <RadixTabs.List
      className={cn('inline-flex gap-0.5 rounded-md border border-strong bg-bg p-[3px]', className)}
      {...props}
    >
      {children}
    </RadixTabs.List>
  )
}

export function TabsTrigger({
  children,
  className,
  ...props
}: React.ComponentProps<typeof RadixTabs.Trigger>) {
  return (
    <RadixTabs.Trigger className={cn(tabsTriggerVariants(), className)} {...props}>
      {children}
    </RadixTabs.Trigger>
  )
}

export function TabsContent({
  children,
  className,
  ...props
}: React.ComponentProps<typeof RadixTabs.Content>) {
  return (
    <RadixTabs.Content
      className={cn('focus-visible:outline-none', className)}
      {...props}
    >
      {children}
    </RadixTabs.Content>
  )
}
