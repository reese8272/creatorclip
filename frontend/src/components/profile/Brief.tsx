import { useMemo } from 'react'
import { parseBrief, type Block, type InlineSpan } from '@/lib/brief'

function Spans({ spans }: { spans: InlineSpan[] }) {
  return (
    <>
      {spans.map((s, i) =>
        s.bold ? (
          <strong key={i} className="font-semibold text-fg">
            {s.text}
          </strong>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
    </>
  )
}

function Blocks({ blocks }: { blocks: Block[] }) {
  return (
    <>
      {blocks.map((b, i) =>
        b.kind === 'p' ? (
          <p key={i} className="text-sm leading-relaxed text-fg/90">
            <Spans spans={b.spans} />
          </p>
        ) : (
          <ul key={i} className="ml-1 flex flex-col gap-1.5">
            {b.items.map((item, j) => (
              <li key={j} className="flex gap-2 text-sm leading-relaxed text-fg/90">
                <span className="mt-2 size-1 shrink-0 rounded-full bg-accent" />
                <span>
                  <Spans spans={item} />
                </span>
              </li>
            ))}
          </ul>
        ),
      )}
    </>
  )
}

// Renders the Creator DNA brief as structured, styled HTML. All text arrives as
// React children (auto-escaped) — no innerHTML on the LLM output.
export function Brief({ markdown }: { markdown: string }) {
  const parsed = useMemo(() => parseBrief(markdown), [markdown])

  return (
    <div className="flex flex-col gap-6">
      {parsed.preamble.length > 0 && (
        <div className="flex flex-col gap-2">
          <Blocks blocks={parsed.preamble} />
        </div>
      )}
      {parsed.sections.map((section, i) => (
        <section key={i} className="flex flex-col gap-2">
          <h3 className="text-2xs font-semibold uppercase tracking-[0.06em] text-accent">
            {section.title}
          </h3>
          <Blocks blocks={section.blocks} />
        </section>
      ))}
    </div>
  )
}
