import * as ts from 'typescript'
import { describe, expect, it } from 'vitest'

import { ancestorJsxTags, formatHits, jsxTagName, scanSources, sourceText } from './sourceScan'

// Issue 384's structural gate: no emoji, dingbat, or geometric glyph may do an
// icon's job anywhere in src/. Icons come from components/ui/icon.tsx.
//
// EVERY PATTERN BELOW USES \u ESCAPES ONLY. A literal glyph in this file would
// make the checker flag itself.
//
// This is a source-scanning test, which is a new pattern for this repo. It earns
// its place because the rule is STRUCTURAL — a rendered-DOM assertion can only
// see the component under test, and the thing being prevented is a glyph
// reappearing in any one of ~200 sites. See sourceScan.ts for why the TypeScript
// AST is used instead of a regex.

// Rule A — emoji, dingbats, geometric shapes, block elements, and the two
// circular-arrow glyphs. Always an icon, never prose. Banned in ALL rendered
// text. The range deliberately starts one block above Box Drawing (used in
// ~2,600 comment banners) and excludes BULLET and MIDDLE DOT, which are
// legitimate typography. U+FE0F (variation selector) is intentionally NOT
// listed: it only ever trails a base character already covered by these ranges,
// and including a combining mark trips no-misleading-character-class.
const PICTOGRAPH =
  /[\u{1F000}-\u{1FAFF}\u{2580}-\u{27BF}\u{2B00}-\u{2BFF}\u{21BA}\u{21BB}\u{27F0}-\u{27FF}]/u

// Rule B — directional arrows and the multiplication sign. These are icons only
// when they name an affordance; between two values they are typography
// ("Setup → peak → end"), so they are banned only in the two positions
// where they are unambiguously doing an icon's job. See INTERACTIVE / isBareGlyph.
const DIRECTIONAL = /[\u{2190}\u{2192}\u{2194}\u{2191}\u{2193}\u{00D7}]/u

// Elements whose text content IS a control's label. `option` is deliberately
// absent: an <option> label like "Score: high → low" is prose.
const INTERACTIVE = new Set(['button', 'a', 'Button', 'Link', 'NavLink'])

// icon.tsx's own header explains why `export *` is forbidden, so these
// assertions must read the CODE, not the prose that documents it.
const ICON_SOURCE = (sourceText('/src/components/ui/icon.tsx') ?? '').replace(/\/\/.*$/gm, '')

/**
 * The icon allow-list, parsed from icon.tsx so the gate can't drift from it.
 * Captures the LOCAL name, so an alias (`Settings as SettingsIcon`) is recorded
 * under the name call sites actually use.
 */
const ICON_NAMES = new Set(
  [...ICON_SOURCE.matchAll(/^ {2}(?:[A-Z][A-Za-z]* as )?([A-Z][A-Za-z]*),$/gm)].map((m) => m[1]),
)

function jsxTextViolations(pattern: RegExp, onlyWhenIconLike: boolean) {
  return scanSources(({ node, report }) => {
    if (!ts.isJsxText(node)) return
    const text = node.text
    if (!pattern.test(text)) return

    if (onlyWhenIconLike) {
      const inInteractive = ancestorJsxTags(node).some((tag) => INTERACTIVE.has(tag))
      // A JsxText that trims to exactly the glyph, with no sibling expression, is
      // standalone decoration: <span>→</span>. With sibling expressions it is
      // a separator: {a} → {b}.
      const parent = node.parent
      const hasExpressionSibling =
        ts.isJsxElement(parent) && parent.children.some((child) => ts.isJsxExpression(child))
      const isBareGlyph = text.trim().length <= 2 && !hasExpressionSibling
      if (!inInteractive && !isBareGlyph) return
    }

    report(node, text.trim().replace(/\s+/g, ' ').slice(0, 80))
  })
}

function literalViolations(pattern: RegExp) {
  return scanSources(({ node, report }) => {
    const isLiteral =
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node)
    if (!isLiteral) return
    const text = (node as ts.LiteralLikeNode).text
    if (!pattern.test(text)) return
    report(node, text.slice(0, 80))
  })
}

describe('no glyph icons (Issue 384)', () => {
  it('renders no emoji, dingbat, or geometric glyph in JSX text', () => {
    const hits = jsxTextViolations(PICTOGRAPH, false)
    expect(hits, `Use components/ui/icon.tsx instead:\n${formatHits(hits)}`).toEqual([])
  })

  it('renders no emoji, dingbat, or geometric glyph in string literals', () => {
    const hits = literalViolations(PICTOGRAPH)
    expect(
      hits,
      `Strings never carry glyphs — the component decides whether to render an ` +
        `icon:\n${formatHits(hits)}`,
    ).toEqual([])
  })

  it('uses no arrow or multiplication glyph as an affordance', () => {
    const hits = jsxTextViolations(DIRECTIONAL, true)
    expect(
      hits,
      `Arrows inside a control label, or standing alone, are icons — use ` +
        `ArrowRight/ArrowLeft/X from components/ui/icon.tsx. Arrows BETWEEN two ` +
        `values are prose and are allowed:\n${formatHits(hits)}`,
    ).toEqual([])
  })

  it('imports lucide-react only through components/ui/icon.tsx', () => {
    const hits = scanSources(
      ({ node, report }) => {
        if (!ts.isImportDeclaration(node)) return
        if (!ts.isStringLiteral(node.moduleSpecifier)) return
        if (!node.moduleSpecifier.text.startsWith('lucide-react')) return
        report(node, node.moduleSpecifier.text)
      },
      (path) => path !== '/src/components/ui/icon.tsx' && path !== '/src/components/ui/iconSizes.ts',
    )
    expect(
      hits,
      `Import icons from '@/components/ui/icon' so the set stays swappable:\n${formatHits(hits)}`,
    ).toEqual([])
  })

  it('keeps icon.tsx a tree-shakeable allow-list', () => {
    expect(ICON_SOURCE, 'icon.tsx must exist').not.toEqual('')
    // A star re-export or a namespace import would defeat Rollup's named-export
    // tracing and ship all ~6,000 lucide icons.
    expect(ICON_SOURCE).not.toMatch(/export\s+\*/)
    expect(ICON_SOURCE).not.toMatch(/import\s+\*\s+as/)
    expect(ICON_NAMES.size).toBeGreaterThan(10)
  })

  it('hides decorative icons from assistive tech', () => {
    // Import-aware rather than name-matching: `Settings` is both a lucide icon
    // and this app's settings page, and `Play`/`Check`/`Star` are plausible
    // component names too. Only a tag actually imported from the icon module is
    // an icon.
    const iconTagsByFile = new Map<string, Set<string>>()
    scanSources(({ node, path }) => {
      if (!ts.isImportDeclaration(node)) return
      if (!ts.isStringLiteral(node.moduleSpecifier)) return
      if (!/components\/ui\/icon$/.test(node.moduleSpecifier.text)) return
      const bindings = node.importClause?.namedBindings
      if (!bindings || !ts.isNamedImports(bindings)) return
      const names = iconTagsByFile.get(path) ?? new Set<string>()
      for (const el of bindings.elements) names.add(el.name.getText(node.getSourceFile()))
      iconTagsByFile.set(path, names)
    })

    const hits = scanSources(({ node, path, report }) => {
      if (!ts.isJsxSelfClosingElement(node) && !ts.isJsxOpeningElement(node)) return
      const tag = jsxTagName(node)
      if (!iconTagsByFile.get(path)?.has(tag)) return
      const named = node.attributes.properties.some(
        (attr) =>
          ts.isJsxAttribute(attr) &&
          (attr.name.getText(node.getSourceFile()) === 'aria-hidden' ||
            attr.name.getText(node.getSourceFile()) === 'aria-label'),
      )
      if (!named) report(node, `<${tag}> needs aria-hidden or aria-label`)
    })
    expect(
      hits,
      `An icon carries no meaning a screen reader should announce unless it is ` +
        `the only label:\n${formatHits(hits)}`,
    ).toEqual([])
  })
})
