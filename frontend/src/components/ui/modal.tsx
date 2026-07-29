import { useEffect, useId, useRef, type ReactNode } from 'react'

interface ModalProps {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
}

// Minimal centered modal with a backdrop. Clicking the backdrop or pressing
// Escape closes it. Implements the WAI-ARIA dialog contract incrementally
// (role/aria-modal/aria-labelledby, initial focus into the panel on open,
// focus restore on close) rather than migrating to native <dialog>, so the
// existing callers' API is untouched.
export function Modal({ open, title, onClose, children }: ModalProps) {
  const titleId = useId()
  const panelRef = useRef<HTMLDivElement>(null)

  // Initial focus into the panel on open; restore focus to the opener when the
  // dialog closes (or unmounts while open).
  useEffect(() => {
    if (!open) return
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    panelRef.current?.focus()
    return () => {
      if (opener && document.contains(opener)) opener.focus()
    }
  }, [open])

  // Escape-to-close; document-level so it works wherever focus sits.
  useEffect(() => {
    if (!open) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[color:var(--color-bg)]/70 p-4 backdrop-blur-sm animate-fade-in"
      onClick={onClose}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="w-full max-w-md rounded-lg border border-strong bg-elevated p-5 shadow-lg outline-none animate-scale-in"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id={titleId} className="mb-3 text-h3 font-ui text-fg">
          {title}
        </h3>
        {children}
      </div>
    </div>
  )
}
