import * as ts from 'typescript'
import { describe, expect, it } from 'vitest'

import { formatHits, jsxTagName, scanSources } from './sourceScan'

// Issue 385's structural gate: no native <select>, checkbox, or radio outside
// components/ui/. Those render with OPERATING-SYSTEM chrome that ignores the
// design system — the OS-blue checkbox against an OKLCH hue-285 palette is the
// visible symptom, and no amount of Tailwind reaches it.
//
// AST-based for the same reason as the glyph gate (see sourceScan.ts): a regex
// for `/<select/` matches the word "Select" inside the comments that explain
// this very rule.

// components/ui/ is the seam that owns the native elements Radix does not render.
const UI_DIR = '/src/components/ui/'

const isNativeSelect = (tag: string): boolean => tag === 'select'

function inputType(node: ts.JsxOpeningElement | ts.JsxSelfClosingElement): string | undefined {
  for (const attr of node.attributes.properties) {
    if (!ts.isJsxAttribute(attr)) continue
    if (attr.name.getText(node.getSourceFile()) !== 'type') continue
    const init = attr.initializer
    if (init && ts.isStringLiteral(init)) return init.text
  }
  return undefined
}

describe('no native form controls (Issue 385)', () => {
  it('renders no native <select> outside components/ui/', () => {
    const hits = scanSources(
      ({ node, report }) => {
        if (!ts.isJsxOpeningElement(node) && !ts.isJsxSelfClosingElement(node)) return
        const tag = jsxTagName(node)
        if (isNativeSelect(tag)) report(node, `<${tag}>`)
      },
      (path) => !path.startsWith(UI_DIR),
    )
    expect(
      hits,
      `Use the Select primitive from '@/components/ui/select':\n${formatHits(hits)}`,
    ).toEqual([])
  })

  it('renders no native checkbox or radio outside components/ui/', () => {
    const hits = scanSources(
      ({ node, report }) => {
        if (!ts.isJsxOpeningElement(node) && !ts.isJsxSelfClosingElement(node)) return
        if (jsxTagName(node) !== 'input') return
        const type = inputType(node)
        if (type === 'checkbox' || type === 'radio') report(node, `<input type="${type}">`)
      },
      (path) => !path.startsWith(UI_DIR),
    )
    expect(
      hits,
      `Use Checkbox / Switch / RadioGroup from components/ui/:\n${formatHits(hits)}`,
    ).toEqual([])
  })

  it('has no ad-hoc select class strings left at call sites', () => {
    // `selectCls` was a bare Tailwind string duplicated per call site, doing the
    // job a primitive should do. Its existence is the smell this issue removes.
    const hits = scanSources(
      ({ node, report }) => {
        if (!ts.isVariableDeclaration(node)) return
        const name = node.name.getText(node.getSourceFile())
        if (/^select(Cls|Class|ClassName)$/.test(name)) report(node, name)
      },
      (path) => !path.startsWith(UI_DIR),
    )
    expect(hits, `Styling belongs in the primitive:\n${formatHits(hits)}`).toEqual([])
  })
})
