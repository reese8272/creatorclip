import * as ts from 'typescript'
import { describe, expect, it } from 'vitest'

import { formatHits, jsxTagName, scanSources } from './sourceScan'

// Issue 386's structural gate. The browser's default control bar is the app
// telling a creator it did not build a player — and its kebab menu exposes
// "Download" and "Picture-in-picture", one of which sits beside a paid action on
// three of our surfaces. It also gives Timeline v2 (#390) nothing to attach to:
// no frame stepping, no shuttle, no app-controlled seek.
//
// components/ui/video-player.tsx is the ONE place a <video> element may exist.
const PLAYER = '/src/components/ui/video-player.tsx'

describe('no native video controls (Issue 386)', () => {
  it('renders <video> only inside the VideoPlayer primitive', () => {
    const hits = scanSources(
      ({ node, report }) => {
        if (!ts.isJsxOpeningElement(node) && !ts.isJsxSelfClosingElement(node)) return
        if (jsxTagName(node) === 'video') report(node, '<video>')
      },
      (path) => path !== PLAYER && !path.endsWith('.test.tsx'),
    )
    expect(
      hits,
      `Use <VideoPlayer> from '@/components/ui/video-player':\n${formatHits(hits)}`,
    ).toEqual([])
  })

  it('never sets the `controls` attribute, including inside the primitive', () => {
    const hits = scanSources(({ node, report }) => {
      if (!ts.isJsxOpeningElement(node) && !ts.isJsxSelfClosingElement(node)) return
      if (jsxTagName(node) !== 'video') return
      for (const attr of node.attributes.properties) {
        if (!ts.isJsxAttribute(attr)) continue
        if (attr.name.getText(node.getSourceFile()) === 'controls') {
          report(node, '<video controls>')
        }
      }
    })
    expect(
      hits,
      `The primitive owns the transport; 'controls' is not a prop:\n${formatHits(hits)}`,
    ).toEqual([])
  })
})
