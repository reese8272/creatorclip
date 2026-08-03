import cssText from 'virtual:design-tokens-css'
import { describe, expect, it } from 'vitest'

import { formatHits, scanSources } from './sourceScan'

// Issue 400a: a utility that names a token which does not exist FAILS SILENTLY.
// Tailwind emits nothing, the element inherits, and the page still renders — so
// neither a type check nor a rendered-DOM assertion can see it. Two live examples
// this test was written from, both shipped for months:
//   - `text-error` (WhyThisClip) — only `--color-danger` exists, so three
//     "Could not generate…" messages rendered in the inherited colour, not red.
//   - `bg-[color:var(--color-border)]` (Nav) — no such token, so the divider
//     between the work and account nav groups was invisible.
//
// index.css is the implementation source of truth (docs/UI.md:14-17), so the
// token list is parsed from it rather than duplicated here.

const CSS = cssText

/** Every `--color-*` custom property declared in the @theme block. */
const COLOR_TOKENS = new Set(
  [...CSS.matchAll(/^\s*--color-([a-z0-9-]+):/gm)].map((m) => m[1]),
)

/** Utility prefixes that resolve against the --color-* namespace. */
const COLOR_UTILITY = new RegExp(
  String.raw`(?<![\w-])(?:bg|text|border|fill|stroke|ring|outline|decoration|shadow|accent|caret|divide|from|via|to)-([a-z][a-z0-9-]*)(?![\w-])`,
  'g',
)

// Tailwind's own palette + keywords that are NOT project tokens and are expected
// to resolve without a --color-* entry.
const BUILTIN = new Set([
  'inherit', 'current', 'transparent', 'black', 'white', 'auto', 'none', 'clip',
  'ellipsis', 'balance', 'pretty', 'wrap', 'nowrap', 'left', 'center', 'right',
  'justify', 'start', 'end', 'top', 'bottom', 'middle', 'solid', 'dashed',
  'dotted', 'double', 'hidden', 'sm', 'md', 'lg', 'xl', 'xs', 'full', 'px',
  'underline', 'overline', 'line-through', 'no-underline', 'opacity', 'offset',
  'inset', 'x', 'y', 'r', 'l', 't', 'b', 'se', 'ss', 'ee', 'es',
])

describe('design-token contract (Issue 400a)', () => {
  it('declares the surface ladder the elevation rules depend on', () => {
    for (const token of ['bg', 'surface', 'elevated', 'raised']) {
      expect(COLOR_TOKENS.has(token), `--color-${token} must exist`).toBe(true)
    }
  })

  it('resolves every var(--color-…) reference in the SPA', () => {
    const hits = scanSources(({ node, path, report }) => {
      if (path.endsWith('.css')) return
      const text = node.getText(node.getSourceFile())
      if (node.getChildCount() > 0) return // leaves only, so each hit reports once
      for (const [, name] of text.matchAll(/var\(--color-([a-z0-9-]+)\)/g)) {
        if (!COLOR_TOKENS.has(name)) report(node, `var(--color-${name}) is not declared`)
      }
    })
    expect(
      hits,
      `A missing token renders as "inherit", not as an error:\n${formatHits(hits)}`,
    ).toEqual([])
  })

  it('uses no colour utility naming a token that was never declared', () => {
    // Scoped to a denylist of names that LOOK like project tokens but are not
    // declared. A blanket check would fight Tailwind's full built-in palette
    // (text-red-500, bg-black/60, …), which is not what this guards.
    const SUSPECT = [
      'error',
      'border',
      'primary',
      'secondary',
      'foreground',
      'background',
      // shadcn's default token names. The crash-recovery screen in App.tsx was
      // written entirely in these and rendered unstyled for months, which is how
      // this list was found — they LOOK like tokens, so they survive review.
      'muted-foreground',
      'primary-foreground',
      'secondary-foreground',
      'accent-foreground',
      'destructive',
      'destructive-foreground',
      'popover',
      'card',
      'input',
      'ring',
    ]
    const undeclared = SUSPECT.filter((name) => !COLOR_TOKENS.has(name))

    const hits = scanSources(({ node, path, report }) => {
      if (path.endsWith('.css') || path.includes('/test/')) return
      if (node.getChildCount() > 0) return
      const text = node.getText(node.getSourceFile())
      for (const [util, name] of text.matchAll(COLOR_UTILITY)) {
        if (BUILTIN.has(name)) continue
        if (undeclared.includes(name)) report(node, `${util} — --color-${name} is not declared`)
      }
    })
    expect(
      hits,
      `Use the declared token (e.g. text-danger, border-default):\n${formatHits(hits)}`,
    ).toEqual([])
  })
})
