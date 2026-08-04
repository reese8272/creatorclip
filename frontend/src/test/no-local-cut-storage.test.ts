import * as ts from 'typescript'
import { describe, expect, it } from 'vitest'

import { formatHits, scanSources, sourcePaths, sourceText } from './sourceScan'

// Issue 391's structural gate.
//
// Cuts used to live in `localStorage['clip:{id}:cuts']`, written by an effect
// whose quota failure was swallowed by `catch { /* quota — recoverable */ }`.
// Clearing site data, switching machines, or opening a second browser destroyed
// the creator's work with no warning.
//
// The document is now server-authoritative and localStorage is a cache. That is
// a property of how the code is WIRED, not of any one rendered output, so a
// component test cannot protect it: someone re-adding a "just persist it locally
// too, it's faster" effect would keep every existing test green while quietly
// restoring the second source of truth. This reads the source tree instead,
// exactly like the Issue 384/385/386/392 gates.
//
// `editDocCache.ts` is the ONE module allowed to touch these keys — it owns the
// one-time legacy import and the paint cache, both of which are deliberately
// subordinate to the server.
const CACHE_MODULE = '/src/lib/editDocCache.ts'

const APP_SOURCES = (path: string) =>
  !path.endsWith('.test.ts') && !path.endsWith('.test.tsx') && !path.startsWith('/src/test/')

describe('cuts are not stored locally (Issue 391)', () => {
  it('only editDocCache touches the cut storage keys', () => {
    const hits = scanSources(({ node, report }) => {
      if (!ts.isStringLiteral(node) && !ts.isTemplateExpression(node)) return
      const text = node.getText()
      // `clip:${clipId}:cuts` and `clip:${clipId}:editdoc`, in either quoting.
      if (!/clip:.*:(cuts|editdoc)/.test(text)) return
      report(node, `cut storage key outside editDocCache: ${text}`)
    }, (path) => APP_SOURCES(path) && path !== CACHE_MODULE)

    expect(
      hits,
      'Cut state is server-authoritative. localStorage is a cache owned by ' +
        `${CACHE_MODULE} — a second writer is a second source of truth:\n${formatHits(hits)}`,
    ).toEqual([])
  })

  it('the editor never writes cuts to localStorage', () => {
    // Narrower and more direct: no setItem call anywhere under components/editor
    // may carry a cut list. The snap PREFERENCE is fine — it is a UI setting,
    // not the creator's work — so this looks at what is being written.
    const hits = scanSources(({ node, sourceFile, report }) => {
      if (!ts.isCallExpression(node)) return
      const callee = node.expression.getText(sourceFile)
      if (!/localStorage\.setItem$/.test(callee)) return
      const arg = node.arguments[1]?.getText(sourceFile) ?? ''
      if (/cut/i.test(arg) || /cut/i.test(node.arguments[0]?.getText(sourceFile) ?? '')) {
        report(node, `editor writes cuts to localStorage: ${node.getText(sourceFile)}`)
      }
    }, (path) => path.startsWith('/src/components/editor/') && APP_SOURCES(path))

    expect(hits, formatHits(hits)).toEqual([])
  })

  it('the glob actually matched the editor sources', () => {
    // Without this the two gates above pass vacuously if the glob pattern or the
    // directory layout ever changes.
    const editorFiles = sourcePaths(
      (p) => p.startsWith('/src/components/editor/') && APP_SOURCES(p),
    )
    expect(editorFiles.length).toBeGreaterThan(5)
    expect(editorFiles).toContain('/src/components/editor/ShortFormEditor.tsx')
    expect(sourceText(CACHE_MODULE)).toBeTruthy()
  })
})
