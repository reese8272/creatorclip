import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createRef } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { VideoPlayer, type VideoPlayerHandle } from './video-player'

/** jsdom leaves `paused` static; make play/pause observable in tests. */
function stubPlayback() {
  const play = vi.fn(function (this: HTMLMediaElement) {
    Object.defineProperty(this, 'paused', { configurable: true, value: false })
    this.dispatchEvent(new Event('play'))
    return Promise.resolve()
  })
  const pause = vi.fn(function (this: HTMLMediaElement) {
    Object.defineProperty(this, 'paused', { configurable: true, value: true })
    this.dispatchEvent(new Event('pause'))
  })
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(play)
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(pause)
  return { play, pause }
}

function renderPlayer(props: Partial<React.ComponentProps<typeof VideoPlayer>> = {}) {
  const ref = createRef<VideoPlayerHandle>()
  const utils = render(<VideoPlayer src="/clips/c1/download" label="Clip preview" ref={ref} {...props} />)
  const video = utils.container.querySelector('video')!
  Object.defineProperty(video, 'duration', { configurable: true, value: 120 })
  fireEvent.durationChange(video)
  return { ...utils, ref, video }
}

describe('VideoPlayer', () => {
  it('never renders the browser control bar', () => {
    const { video } = renderPlayer()
    expect(video).not.toHaveAttribute('controls')
  })

  it('is a named group, so no player ships anonymous', () => {
    renderPlayer()
    expect(screen.getByRole('group', { name: 'Clip preview' })).toBeInTheDocument()
  })

  it('toggles playback and relabels the button', async () => {
    const { play, pause } = stubPlayback()
    const user = userEvent.setup()
    renderPlayer()

    await user.click(screen.getByRole('button', { name: 'Play' }))
    expect(play).toHaveBeenCalled()
    await user.click(await screen.findByRole('button', { name: 'Pause' }))
    expect(pause).toHaveBeenCalled()
  })

  it('exposes the scrub bar as a slider with a readable value', () => {
    renderPlayer()
    const slider = screen.getByRole('slider', { name: 'Seek' })
    expect(slider).toHaveAttribute('aria-valuemax', '120')
    expect(slider).toHaveAttribute('aria-valuetext', '0:00 of 2:00')
  })

  it('seeks by ±5s on arrow keys', async () => {
    const user = userEvent.setup()
    const { ref, video } = renderPlayer({ transport: true })
    ref.current!.seek(30)

    await user.keyboard('{ArrowRight}')
    expect(video.currentTime).toBe(35)
    await user.keyboard('{ArrowLeft}')
    expect(video.currentTime).toBe(30)
  })

  it('steps one frame on , and . — and pauses first', async () => {
    const { pause } = stubPlayback()
    const user = userEvent.setup()
    const { ref, video } = renderPlayer({ transport: true, fps: 25 })
    ref.current!.seek(10)

    await user.keyboard('.')
    expect(video.currentTime).toBeCloseTo(10.04, 5)
    expect(pause).toHaveBeenCalled()

    await user.keyboard(',')
    expect(video.currentTime).toBeCloseTo(10, 5)
  })

  it('shuttles with L and resets with K', async () => {
    stubPlayback()
    const user = userEvent.setup()
    const { video } = renderPlayer({ transport: true })

    await user.keyboard('l')
    expect(video.playbackRate).toBe(1.5)
    await user.keyboard('l')
    expect(video.playbackRate).toBe(2)

    await user.keyboard('k')
    expect(video.playbackRate).toBe(1)
  })

  // THE KEY-THEFT GUARD. `transport` binds a document-level listener; if its
  // bail-out is ever weakened, typing a space into any field on the page starts
  // playing video. Two shapes: a real input, and the editor's transcript surface,
  // which is a role="textbox" div rather than an input.
  it('does not steal keys from text surfaces', async () => {
    const { play } = stubPlayback()
    const user = userEvent.setup()
    render(
      <>
        <VideoPlayer src="/x" label="Clip" transport />
        <input aria-label="Title" />
        <div role="textbox" aria-readonly tabIndex={0} aria-label="Transcript" />
      </>,
    )

    await user.click(screen.getByRole('textbox', { name: 'Title' }))
    await user.keyboard(' ')
    expect(play).not.toHaveBeenCalled()

    await user.click(screen.getByRole('textbox', { name: 'Transcript' }))
    await user.keyboard(' ')
    expect(play).not.toHaveBeenCalled()
  })

  it('does not steal keys while a modal dialog is open', async () => {
    const { play } = stubPlayback()
    const user = userEvent.setup()
    render(
      <>
        <VideoPlayer src="/x" label="Clip" transport />
        <div role="dialog" aria-modal="true" aria-label="Confirm" />
      </>,
    )

    await user.keyboard(' ')
    expect(play).not.toHaveBeenCalled()
  })

  // Ported from ClipPlayer.test.tsx — the Issue 359d autoplay-block fix.
  it('mutes when autoPlay is set, so the browser does not block it', () => {
    const { container } = render(<VideoPlayer src="/x" label="Clip" autoPlay />)
    const video = container.querySelector('video')!
    expect(video).toHaveAttribute('autoplay')
    expect(video).toHaveAttribute('preload', 'auto')
    expect(video.muted).toBe(true)
  })

  // Ported from ClipPlayer.test.tsx — a re-render must not keep stale media when
  // the artifact behind an unchanged URL has been replaced.
  it('remounts the element when mediaKey changes', () => {
    const { container, rerender } = render(
      <VideoPlayer src="/clips/c1/download" label="Clip" mediaKey="v1" />,
    )
    const first = container.querySelector('video')
    rerender(<VideoPlayer src="/clips/c1/download" label="Clip" mediaKey="v2" />)
    expect(container.querySelector('video')).not.toBe(first)
  })

  it('reports time through the ref without re-rendering the consumer', () => {
    const renders = vi.fn()
    function Consumer() {
      renders()
      return <VideoPlayer src="/x" label="Clip" />
    }
    const { container } = render(<Consumer />)
    const video = container.querySelector('video')!
    Object.defineProperty(video, 'currentTime', { configurable: true, value: 12 })
    fireEvent.timeUpdate(video)
    // The page did not re-render; only the player's own chrome did.
    expect(renders).toHaveBeenCalledTimes(1)
  })

  it('notifies subscribers on seek', () => {
    const { ref } = renderPlayer()
    const cb = vi.fn()
    const unsubscribe = ref.current!.subscribeTime(cb)
    ref.current!.seek(42)
    expect(cb).toHaveBeenCalled()
    expect(ref.current!.getCurrentTime()).toBe(42)
    unsubscribe()
    ref.current!.seek(7)
    expect(cb).toHaveBeenCalledTimes(1)
  })

  it('forwards onError and onPlay to the consumer', async () => {
    stubPlayback()
    const onError = vi.fn()
    const onPlay = vi.fn()
    const user = userEvent.setup()
    const { container } = render(
      <VideoPlayer src="/x" label="Clip" onError={onError} onPlay={onPlay} />,
    )
    fireEvent.error(container.querySelector('video')!)
    expect(onError).toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Play' }))
    expect(onPlay).toHaveBeenCalled()
  })
})
