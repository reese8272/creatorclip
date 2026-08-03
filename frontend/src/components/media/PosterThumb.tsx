import { useState } from 'react'

import { cn } from '@/lib/utils'
import { Chip } from '@/components/Chip'

// PosterThumb — a video's poster frame, or an informative placeholder (Issue 387).
//
// The video library was a text table. Creators identify footage visually; a row
// reading `Desk Setup Tour 2026 · long · xyz98765432` requires reading and
// recall, where a poster frame is recognised instantly.
//
// SIZING LIVES AT THE CALL SITE, via className. That is deliberate and is what
// keeps this reusable by #398's library grid and #399's clip cards without
// modification — this component owns the aspect box, the loading behaviour and
// the fallback, not the layout.
export interface PosterThumbProps {
  /** Authed poster URL, or null when the video has none. */
  src: string | null
  alt: string
  /** Why there is no poster — drives the placeholder copy. */
  reason?: 'pending' | 'expired' | 'none'
  className?: string
}

const PLACEHOLDER_COPY: Record<NonNullable<PosterThumbProps['reason']>, string> = {
  // "Not yet" and "never" are different situations and a creator can act on the
  // difference, so the placeholder says which it is rather than showing a
  // uniform grey rectangle.
  pending: 'Processing',
  expired: 'Source expired',
  none: 'No preview',
}

export function PosterThumb({ src, alt, reason = 'none', className }: PosterThumbProps) {
  const [failed, setFailed] = useState(false)
  const showImage = src !== null && !failed

  return (
    <div
      className={cn(
        // @container so the PLACEHOLDER can adapt to whatever size the call site
        // picks. A 16:9 box at w-16 is only 36px tall — an icon plus two lines of
        // label does not fit, and the label clipped mid-word.
        '@container relative flex aspect-video shrink-0 items-center justify-center overflow-hidden rounded-sm border border-default bg-elevated',
        className,
      )}
    >
      {showImage ? (
        <img
          src={src}
          alt={alt}
          loading="lazy"
          decoding="async"
          // Explicit intrinsic size + the aspect box above reserve layout, so
          // rows do not jump as posters stream in.
          width={640}
          height={360}
          // object-cover handles vertical uploads without letterboxing.
          className="h-full w-full object-cover"
          // A poster deleted out of band, or a race between the list response
          // and the image request, degrades to the placeholder rather than a
          // broken-image glyph.
          onError={() => setFailed(true)}
        />
      ) : (
        <span
          className="flex flex-col items-center gap-1 px-1 text-center"
          // The icon alone is the fallback at small sizes; the words are the
          // detail, so they are what gives way. title= keeps the reason
          // available on hover either way.
          title={PLACEHOLDER_COPY[reason]}
        >
          <Chip pose="papers" size={20} />
          <span className="hidden text-label leading-tight text-muted @min-[7rem]:block">
            {PLACEHOLDER_COPY[reason]}
          </span>
        </span>
      )}
    </div>
  )
}
