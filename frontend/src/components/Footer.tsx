import { LegalLinks } from '@/components/LegalLinks'

export function Footer() {
  return (
    <footer className="mt-12 flex items-center gap-4 border-t border-default px-6 py-5 text-xs text-subtle">
      <LegalLinks />
    </footer>
  )
}
