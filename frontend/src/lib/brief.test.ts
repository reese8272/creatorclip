import { describe, expect, it } from 'vitest'
import { parseBrief, parseInline } from './brief'

// The brief parser turns Claude's markdown (dna/brief.py) into a structured
// tree the renderer maps to React elements. These lock the load-bearing
// behaviour: section splitting, inline emphasis, bullets, and the XSS-safety
// boundary (the parser never produces markup — only text + structure).

const SAMPLE = `1. **Channel Signature** — You make calm, data-driven finance explainers.
2. **What's Driving Views**
- Strong cold opens in the first 3 seconds
- Clips that land between 30 and 45 seconds
3. **Where to Improve**
Your bottom performers bury the payoff past the 20s mark.
4. **Optimal Clip Profile** — 38s clips from the mid-section.
5. **Shorts Strategy** — Not enough Shorts data yet.`

describe('parseBrief', () => {
  it('splits the five known sections by their bold headers', () => {
    const { sections } = parseBrief(SAMPLE)
    expect(sections.map((s) => s.title)).toEqual([
      'Channel Signature',
      "What's Driving Views",
      'Where to Improve',
      'Optimal Clip Profile',
      'Shorts Strategy',
    ])
  })

  it('captures trailing text on the heading line as a paragraph', () => {
    const { sections } = parseBrief(SAMPLE)
    const sig = sections[0]
    expect(sig.blocks[0]).toEqual({
      kind: 'p',
      spans: [{ text: 'You make calm, data-driven finance explainers.', bold: false }],
    })
  })

  it('parses bullet lists under a section', () => {
    const { sections } = parseBrief(SAMPLE)
    const driving = sections[1]
    const ul = driving.blocks.find((b) => b.kind === 'ul')
    expect(ul).toBeDefined()
    expect(ul?.kind === 'ul' && ul.items.length).toBe(2)
  })

  it('handles an empty / missing brief without throwing', () => {
    expect(parseBrief('')).toEqual({ preamble: [], sections: [] })
  })
})

describe('parseInline', () => {
  it('splits bold runs from plain text', () => {
    expect(parseInline('plain **bold** more')).toEqual([
      { text: 'plain ', bold: false },
      { text: 'bold', bold: true },
      { text: ' more', bold: false },
    ])
  })

  it('keeps HTML-looking content as inert text (no markup is ever produced)', () => {
    // A creator video title embedded in the brief could contain markup. The
    // parser must keep it as a text span — the renderer puts it in a React
    // child, so it is escaped, never interpreted. This is the structural
    // XSS guarantee (OWASP DOM-XSS guidance; docs/DECISIONS.md 2026-06-17).
    const evil = 'My video <img src=x onerror=alert(1)>'
    const spans = parseInline(evil)
    expect(spans).toEqual([{ text: evil, bold: false }])
  })
})
