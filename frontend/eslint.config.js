import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist', 'e2e/.results', 'e2e/.report']),
  // React SPA source — full React Hooks + Fast Refresh rules.
  {
    files: ['src/**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      // Honour the `_`-prefix convention for intentionally-unused params/vars
      // (e.g. fake-event-listener signatures). Without this, `_type`/`_cb` are
      // flagged despite the underscore signalling intent. (Issue 204 cleanup.)
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],
      // Issue 384: components/ui/icon.tsx is the single seam to the icon
      // library, so the whole set stays swappable from one file. It re-exports
      // an explicit allow-list, which is also what keeps the bundle tree-shaken.
      'no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: 'lucide-react',
              message: "Import icons from '@/components/ui/icon' instead.",
            },
          ],
        },
      ],
    },
  },
  // The icon seam itself is the one place allowed to reach the library.
  {
    files: ['src/components/ui/icon.tsx', 'src/components/ui/iconSizes.ts'],
    rules: { 'no-restricted-imports': 'off' },
  },
  // Node-side tooling: Playwright E2E specs/fixtures + config. No React rules —
  // the react-hooks plugin otherwise false-positives on Playwright's `use()`
  // fixture callback (it is not a React Hook).
  {
    files: ['e2e/**/*.ts', '*.config.{ts,js}'],
    extends: [js.configs.recommended, tseslint.configs.recommended],
    languageOptions: {
      globals: { ...globals.node, ...globals.browser },
    },
  },
])
