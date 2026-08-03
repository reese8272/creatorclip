/// <reference types="vite/client" />

// Plain-text copy of src/index.css, served by the `design-tokens-text` plugin in
// vite.config.ts. Used only by src/test/design-tokens.contract.test.ts — a `?raw`
// import cannot reach it because `test.css: false` and @tailwindcss/vite both
// claim .css first.
declare module 'virtual:design-tokens-css' {
  const css: string
  export default css
}
