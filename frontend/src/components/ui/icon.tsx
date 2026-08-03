// The single seam between the app and the icon library (Issue 384).
//
// Every icon in the SPA is imported from HERE, never from 'lucide-react'
// directly — enforced by the no-restricted-imports rule in eslint.config.js and
// by src/test/no-glyph-icons.test.ts — so the whole set is swappable by editing
// this one file.
//
// TREE-SHAKING: this is an explicit ALLOW-LIST of named re-exports, never
// `export *` and never a runtime name→component map. lucide-react is
// sideEffects:false with an ESM entry (dist/esm/lucide-react.mjs), so Rollup's
// named-export tracing drops every name below that no call site imports. The
// barrel is a COMPILE-TIME alias, not a runtime one. `export *` or a dynamic
// lookup would defeat that analysis and ship all ~6,000 icons.
//
// Sizes and types live in ./iconSizes.ts because this file must export only
// components (react-refresh/only-export-components) — the same reason
// buttonVariants.ts is split out of button.tsx.
export {
  // Direction — CTA affordances and trend deltas.
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  ArrowDown,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  TrendingUp,
  TrendingDown,
  Minus,

  // State markers.
  Check,
  Circle,
  TriangleAlert,

  // Dismiss / navigation chrome.
  X,
  Menu,

  // Verdict + clip actions.
  ThumbsUp,
  ThumbsDown,
  Scissors,
  Download,
  Play,
  RotateCcw,
  RefreshCw,

  // Aspect-mode toggles (short-form 9:16 vs long-form 16:9).
  RectangleVertical,
  RectangleHorizontal,

  // Accents.
  Star,
  Sparkles,
  // Aliased: `Settings` is also this app's settings page component.
  Settings as SettingsIcon,
} from 'lucide-react'
