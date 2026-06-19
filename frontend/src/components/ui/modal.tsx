import type { ReactNode } from 'react'

interface ModalProps {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
}

// Minimal centered modal with a backdrop. Clicking the backdrop closes it.
export function Modal({ open, title, onClose, children }: ModalProps) {
  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[color:var(--color-bg)]/70 p-4 backdrop-blur-sm animate-fade-in"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg border border-strong bg-elevated p-5 shadow-lg animate-scale-in"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-3 text-h3 font-ui text-fg">{title}</h3>
        {children}
      </div>
    </div>
  )
}
