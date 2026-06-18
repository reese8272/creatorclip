import { cva, type VariantProps } from 'class-variance-authority'
import type { ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 rounded-md font-medium whitespace-nowrap transition-colors duration-100 ease-snappy focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-soft disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        primary: 'bg-accent text-on-accent hover:bg-accent-hover',
        secondary: 'border border-strong bg-surface text-fg hover:bg-elevated',
        confirm: 'bg-success text-bg hover:opacity-90',
        outline: 'border border-strong bg-transparent text-fg hover:border-accent hover:text-accent',
        danger: 'border border-[color:var(--color-danger-border)] bg-[color:var(--color-danger-soft)] text-danger hover:bg-danger hover:text-bg',
        ghost: 'text-muted hover:text-fg hover:bg-elevated',
      },
      size: {
        default: 'h-9 px-4 text-sm',
        sm: 'h-7 px-3 text-xs',
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
