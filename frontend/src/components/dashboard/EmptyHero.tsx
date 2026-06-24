import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'

const STEPS = [
  {
    num: 1,
    title: 'Upload your video file',
    sub: 'Your raw file is the source — we transcribe it and surface clips ranked by fit. We never download from YouTube.',
  },
  {
    num: 2,
    title: 'Stream from OBS',
    sub: 'Already recording? Generate an API key and your local recordings ingest automatically.',
  },
  {
    num: 3,
    title: 'Take the walkthrough',
    sub: 'A short tour of how scoring works, what Creator DNA does, and where your data lives.',
  },
]

// Authenticated zero-video state. Mirrors the pre-auth hero copy so the
// first-login user lands on an explainer with three concrete next steps instead
// of an empty table. "Upload a video" expands the UploadVideoForm in place.
export function EmptyHero({ onUploadClick }: { onUploadClick: () => void }) {
  return (
    <section
      aria-label="Get started"
      className="my-6 animate-slide-up rounded-xl border border-default bg-surface px-7 py-8 shadow-sm shadow-inset"
    >
      <h2 className="mb-2 text-h2 text-fg">Let's get your first clip.</h2>
      <p className="mb-5 max-w-[60ch] text-sm leading-relaxed text-muted">
        AutoClip needs one video to learn from before it can rank clips against your channel's DNA.
        Upload your source file or stream from OBS — both land in the same review queue.
      </p>
      <div className="mb-5 grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
        {STEPS.map((s) => (
          <div key={s.num} className="rounded-lg border border-default bg-bg p-4">
            <span className="mb-2 inline-flex h-[22px] w-[22px] items-center justify-center rounded-full bg-accent-soft font-mono text-xs font-semibold text-accent-text">
              {s.num}
            </span>
            <div className="mb-1 text-sm font-semibold text-fg">{s.title}</div>
            <div className="text-sm leading-relaxed text-muted">{s.sub}</div>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-3">
        <Button onClick={onUploadClick}>Upload a video →</Button>
        <Link to="/profile">
          <Button variant="secondary">Get your API key</Button>
        </Link>
        <Link to="/walkthrough">
          <Button variant="secondary">Open walkthrough</Button>
        </Link>
      </div>
      <p className="mt-4 text-xs text-subtle">
        AutoClip predicts fit with your style and audience — it does not promise virality. All
        recommendations are estimates grounded in your own data.
      </p>
    </section>
  )
}
