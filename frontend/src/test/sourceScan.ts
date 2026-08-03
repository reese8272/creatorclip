import * as ts from 'typescript'

// Shared source-scanning harness for the structural gates (Issues 384/385).
//
// WHY import.meta.glob AND NOT node:fs — src/** compiles under
// tsconfig.app.json with `types: ["vite/client"]` and no @types/node, so a
// `node:fs` import fails `tsc -b`, which is `npm run build`, which is a CI gate.
// import.meta.glob is typed by vite/client and needs no config change.
//
// WHY THE TYPESCRIPT AST AND NOT A REGEX — comments are parsed as *trivia* and
// never become AST nodes. src/ contains ~2,600 box-drawing characters in comment
// banners, `2×2` inside comments, and a dozen arrows in prose comments
// ("analyze → show → save"); every one of those is a false positive for a
// raw-text regex, and comment-stripping by regex breaks on `https://` inside
// string literals. Walking JsxText plus string/template literals gives exact
// coverage of "text that can reach the DOM".
//
// The namespace import form (`import * as ts`) is required: tsconfig.app.json
// does not set esModuleInterop and the `typescript` package is `export =`.

const RAW = import.meta.glob('/src/**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

// Stylesheets are globbed separately: they are not parsed as TypeScript, but the
// design-token contract test needs to read the @theme block that declares the
// tokens every utility resolves against.
const RAW_CSS = import.meta.glob('/src/**/*.css', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

export interface Hit {
  file: string
  line: number
  text: string
}

export interface ScanContext {
  node: ts.Node
  sourceFile: ts.SourceFile
  path: string
  /** Records a violation at `node`'s position. */
  report: (node: ts.Node, text: string) => void
}

/** Every scanned source path, so a gate can assert the glob matched anything. */
export function sourcePaths(filter: (path: string) => boolean = () => true): string[] {
  return Object.keys(RAW).filter(filter).sort()
}

/** Raw text of one scanned file, for assertions that don't need the AST. */
export function sourceText(path: string): string | undefined {
  return RAW[path] ?? RAW_CSS[path]
}

/**
 * Parse every matching source file and run `visit` over each node in it.
 * Returns the accumulated hits, sorted by file then line.
 */
export function scanSources(
  visit: (ctx: ScanContext) => void,
  filter: (path: string) => boolean = () => true,
): Hit[] {
  const hits: Hit[] = []

  for (const path of sourcePaths(filter)) {
    const sourceFile = ts.createSourceFile(
      path,
      RAW[path],
      ts.ScriptTarget.ESNext,
      /* setParentNodes */ true,
      path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
    )

    const report = (node: ts.Node, text: string): void => {
      const { line } = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile))
      hits.push({ file: path, line: line + 1, text })
    }

    const walk = (node: ts.Node): void => {
      visit({ node, sourceFile, path, report })
      ts.forEachChild(node, walk)
    }
    walk(sourceFile)
  }

  return hits.sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line)
}

/** Tag name of a JSX element, e.g. `button`, `Button`, `Link`. */
export function jsxTagName(node: ts.JsxOpeningElement | ts.JsxSelfClosingElement): string {
  return node.tagName.getText(node.getSourceFile())
}

/** Nearest enclosing JSX element's tag name, or undefined at the top level. */
export function enclosingJsxTag(node: ts.Node): string | undefined {
  for (let cur = node.parent; cur; cur = cur.parent) {
    if (ts.isJsxElement(cur)) return jsxTagName(cur.openingElement)
  }
  return undefined
}

/** Every enclosing JSX element's tag name, innermost first. */
export function ancestorJsxTags(node: ts.Node): string[] {
  const tags: string[] = []
  for (let cur = node.parent; cur; cur = cur.parent) {
    if (ts.isJsxElement(cur)) tags.push(jsxTagName(cur.openingElement))
  }
  return tags
}

/** Formats hits for an assertion message that points straight at the source. */
export function formatHits(hits: Hit[]): string {
  return hits.map((h) => `${h.file}:${h.line} — ${h.text}`).join('\n')
}
