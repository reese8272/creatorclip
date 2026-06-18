import type { ReactNode } from 'react'

// One step of the onboarding stack: a numbered card with a title, optional
// "(optional)"-style meta, and a body. Presentational only — all flow logic
// lives in the Onboarding page.
export function StepCard({
  num,
  title,
  meta,
  children,
}: {
  num: number
  title: string
  meta?: string
  children: ReactNode
}) {
  return (
    <section className="rounded-md border border-default bg-surface p-5">
      <h2 className="mb-3 flex items-center gap-2 text-base font-medium text-fg">
        <span className="inline-flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-accent-soft font-mono text-xs font-semibold text-accent">
          {num}
        </span>
        {title}
        {meta && <span className="text-xs font-normal text-subtle">{meta}</span>}
      </h2>
      {children}
    </section>
  )
}
