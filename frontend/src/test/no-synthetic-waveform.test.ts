import * as ts from 'typescript'
import { describe, expect, it } from 'vitest'

import { formatHits, scanSources } from './sourceScan'

// Issue 392's structural gate.
//
// The long-form "Source timeline" used to draw 48 bars at `20 + ((i * 37) % 60)%`
// — a deterministic arithmetic pattern with no relationship to the audio — under
// a label telling the creator it was their source. It is the one defect in the
// lane that asserts something FALSE about the user's own content, on the surface
// whose entire job is helping them find moments.
//
// A rendered-DOM assertion cannot prevent its return: it would only see the
// component under test, and any timeline anywhere could grow a new "placeholder
// wave" that looks plausible. So this reads the source tree, like the four gates
// Issue 384/385/386 added.
//
// The rule: inside the editor components, a height/amplitude style value may not
// be computed from a bare loop INDEX. Real amplitude comes from `peakEnvelope`.
const EDITOR_SOURCES = (path: string) =>
  path.startsWith('/src/components/editor/') && !path.endsWith('.test.tsx')

// Identifiers that legitimately index geometry rather than fake amplitude —
// positioning a chapter tick or a cut overlay along the time axis is fine.
const TIME_LIKE = /start|end|at\b|left|width|time|dur|ratio|pct|percent|scroll|x\b/i

describe('no synthetic waveform amplitude (Issue 392)', () => {
  it('never derives a bar height from a loop index', () => {
    const hits = scanSources(({ node, sourceFile, report }) => {
      // Look at object literal properties named `height` (style objects) and at
      // template expressions building a `height:` CSS string.
      if (!ts.isPropertyAssignment(node)) return
      const name = node.name.getText(sourceFile)
      if (!/^height$/i.test(name)) return
      const value = node.initializer.getText(sourceFile)
      // `% 60`, `* 37`, `i * ...` — arithmetic over a single-letter counter is
      // the signature of fabricated amplitude.
      if (/\b[ij]\b\s*[*%+]/.test(value) && !TIME_LIKE.test(value)) {
        report(node, `height computed from a loop index: ${value}`)
      }
    }, EDITOR_SOURCES)

    expect(
      hits,
      'A waveform must come from real peaks (lib/peaks.ts → peakEnvelope) or render ' +
        `the honest flat track. Never generate amplitude:\n${formatHits(hits)}`,
    ).toEqual([])
  })

  // NOT ADDED: a raw-text check for the literal `(i * 37) % 60` string. It was
  // written, and it failed — on the three comments (here, in Waveform.tsx and in
  // lib/peaks.ts) that quote the removed generator to explain why it was removed.
  // That is precisely the false-positive mode sourceScan.ts's own docstring warns
  // about: comments are trivia and never become AST nodes, so a regex over raw
  // text cannot tell an explanation from an offence. The AST rule above catches
  // the actual shape (`style={{ height: \`${20 + ((i * 37) % 60)}%\` }}` is a
  // PropertyAssignment named `height` whose initializer does arithmetic on `i`),
  // and deleting a comment to satisfy a test would be the wrong trade.

  it('the Waveform component is the only thing that paints a waveform', () => {
    // If a second painter appears, this gate stops applying to it — which is how
    // the fabricated one would sneak back.
    //
    // AST, not raw text (Issue 390). This started as a regex over file contents
    // and false-positived on TimelineRuler.tsx's own comment explaining why it is
    // deliberately NOT a canvas — the second time a raw-text scan in this file
    // has flagged an explanation rather than an offence. Matching a CallExpression
    // whose callee property is `getContext` cannot see comments at all, because
    // comments are trivia and never become AST nodes.
    const painters = new Set(
      scanSources(({ node, sourceFile, report, path }) => {
        if (!ts.isCallExpression(node)) return
        const callee = node.expression
        if (!ts.isPropertyAccessExpression(callee)) return
        if (callee.name.getText(sourceFile) !== 'getContext') return
        report(node, path)
      }, EDITOR_SOURCES).map((hit) => hit.file),
    )
    expect([...painters]).toEqual(['/src/components/editor/Waveform.tsx'])
  })
})
