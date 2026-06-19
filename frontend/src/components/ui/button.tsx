import { cva, type VariantProps } from 'class-variance-authority'
import type { ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  // radius-sm (6px) per docs/UI.md; motion: standard-eased color+shadow+transform
  // with an active:scale press cue; focus ring uses --color-accent-border. The
  // shadow-inset top-edge highlight on filled variants restores the 3D affordance
  // flat dark surfaces lose (docs/UI.md "Shadows").
  'inline-flex items-center justify-center gap-2 rounded-sm font-ui font-medium whitespace-nowrap transition-[background-color,border-color,box-shadow,transform] duration-fast ease-standard active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-border focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:pointer-events-none disabled:opacity-50 disabled:shadow-none',
  {
    variants: {
      variant: {
        primary: 'bg-accent text-on-accent shadow-sm shadow-inset hover:bg-accent-hover active:bg-accent-active',
        secondary: 'border border-strong bg-surface text-fg shadow-inset hover:bg-elevated',
        confirm: 'bg-success text-bg shadow-sm shadow-inset hover:opacity-90',
        outline: 'border border-strong bg-transparent text-fg hover:border-accent hover:text-accent-text',
        danger: 'border border-[color:var(--color-danger-border)] bg-[color:var(--color-danger-soft)] text-danger hover:bg-danger hover:text-bg',
        ghost: 'text-muted hover:text-fg hover:bg-elevated',
      },
      size: {
        default: 'h-9 px-4 text-body',
        sm: 'h-7 px-3 text-small',
      },
    },
    defaultVariants: { variant: 'primary', size: 'default' },
  },
)

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export function Button({ className, variant, size, ...props }: ButtonProps) {
  return <button className={cn(buttonVariants({ variant, size }), className)} {...props} />
}
