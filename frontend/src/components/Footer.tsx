export function Footer() {
  return (
    <footer className="mt-12 flex items-center gap-4 border-t border-default px-6 py-5 text-xs text-subtle">
      <a href="/static/tos.html" className="hover:text-fg">
        Terms
      </a>
      <a href="/static/privacy.html" className="hover:text-fg">
        Privacy
      </a>
      <a href="/static/accessibility.html" className="hover:text-fg">
        Accessibility
      </a>
      <span>© AutoClip 2026</span>
    </footer>
  )
}
