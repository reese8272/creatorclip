// Parser for the Creator DNA brief.
//
// The brief comes back from Claude as plain Markdown with a known shape: up to
// five numbered sections, each headed `N. **Title**`, followed by prose and/or
// bullet lists, with `**bold**` inline emphasis (see dna/brief.py). The vanilla
// page dumped `brief_text` straight into `textContent`, so the `**` and `1.`
// rendered literally — that is the "wall of asterisks" look.
//
// We parse it into a small structured tree that the renderer turns into real
// React elements. Because every text fragment becomes a React child (never
// innerHTML), all content is auto-escaped — so creator-supplied video titles
// embedded in the brief cannot inject markup. This is the OWASP DOM-XSS
// guidance (textContent over innerHTML) implemented structurally; see
// docs/DECISIONS.md (2026-06-17).

export interface InlineSpan {
  text: string
  bold: boolean
}

export type Block =
  | { kind: 'p'; spans: InlineSpan[] }
  | { kind: 'ul'; items: InlineSpan[][] }

export interface BriefSection {
  title: string
  blocks: Block[]
}

export interface ParsedBrief {
  preamble: Block[]
  sections: BriefSection[]
}

const SECTION_RE = /^\s*(?:#{1,6}\s+|\d+\.\s+)?\*\*(.+?)\*\*\s*[:—–-]?\s*(.*)$/
const BULLET_RE = /^\s*[-*]\s+(.*)$/

// Split a line on `**` markers into bold / non-bold spans.
export function parseInline(text: string): InlineSpan[] {
  const spans: InlineSpan[] = []
  const parts = text.split(/\*\*/)
  parts.forEach((part, i) => {
    if (part === '') return
    spans.push({ text: part, bold: i % 2 === 1 })
  })
  return spans.length ? spans : [{ text, bold: false }]
}

// Is this line a section heading? Returns [title, trailingText] or null.
function matchHeading(line: string): [string, string] | null {
  const m = line.match(SECTION_RE)
  if (!m) return null
  // Only treat as a heading when the bold runs to the start of the meaningful
  // content (avoids mid-paragraph **bold** being mistaken for a section).
  const before = line.slice(0, line.indexOf('**')).trim()
  if (before && !/^(#{1,6}|\d+\.)$/.test(before)) return null
  return [m[1].trim(), m[2].trim()]
}

function flushParagraph(buf: string[], blocks: Block[]): void {
  const text = buf.join(' ').trim()
  if (text) blocks.push({ kind: 'p', spans: parseInline(text) })
  buf.length = 0
}

function flushBullets(items: string[], blocks: Block[]): void {
  if (items.length) {
    blocks.push({ kind: 'ul', items: items.map(parseInline) })
  }
  items.length = 0
}

function parseBlocks(lines: string[]): Block[] {
  const blocks: Block[] = []
  const para: string[] = []
  const bullets: string[] = []

  for (const raw of lines) {
    const line = raw.trim()
    const bullet = line.match(BULLET_RE)
    if (bullet) {
      flushParagraph(para, blocks)
      bullets.push(bullet[1])
      continue
    }
    if (line === '') {
      flushParagraph(para, blocks)
      flushBullets(bullets, blocks)
      continue
    }
    flushBullets(bullets, blocks)
    para.push(line)
  }
  flushParagraph(para, blocks)
  flushBullets(bullets, blocks)
  return blocks
}

export function parseBrief(markdown: string): ParsedBrief {
  const lines = (markdown || '').replace(/\r\n/g, '\n').split('\n')
  const sections: BriefSection[] = []
  const preambleLines: string[] = []
  let current: { title: string; lines: string[] } | null = null

  for (const line of lines) {
    const heading = matchHeading(line)
    if (heading) {
      if (current) sections.push({ title: current.title, blocks: parseBlocks(current.lines) })
      current = { title: heading[0], lines: heading[1] ? [heading[1]] : [] }
    } else if (current) {
      current.lines.push(line)
    } else {
      preambleLines.push(line)
    }
  }
  if (current) sections.push({ title: current.title, blocks: parseBlocks(current.lines) })

  return { preamble: parseBlocks(preambleLines), sections }
}
