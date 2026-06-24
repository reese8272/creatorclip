import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Chip } from './Chip'
import { CHIP_POSES } from './chip/poses'
import {
  ChipAnalyzing,
  ChipGeneratingClips,
  ChipLoadingScreen,
  ChipLookingItUp,
  ChipPersonalizing,
  ChipRendering,
  ChipStreaming,
  ChipThinking,
} from './chip/ChipStates'

describe('Chip', () => {
  it('renders the sprite for the requested pose from /chip/', () => {
    const { container } = render(<Chip pose="magnify" />)
    const img = container.querySelector('img')!
    expect(img).toHaveAttribute('src', '/chip/chip-magnify.png')
  })

  it('is decorative: empty alt + aria-hidden so screen readers skip it (W3C WAI)', () => {
    const { container } = render(<Chip pose="think" />)
    const img = container.querySelector('img')!
    expect(img).toHaveAttribute('alt', '')
    expect(img).toHaveAttribute('aria-hidden', 'true')
  })

  it('applies the requested size to width and height', () => {
    const { container } = render(<Chip pose="book" size={96} />)
    const img = container.querySelector('img')!
    expect(img).toHaveAttribute('width', '96')
    expect(img).toHaveAttribute('height', '96')
  })

  it('exposes the full concept→pose map', () => {
    expect(Object.keys(CHIP_POSES)).toHaveLength(10)
  })
})

describe('Chip animation states', () => {
  it('render without crashing and embed a Chip sprite', () => {
    const states = [
      <ChipAnalyzing key="a" />,
      <ChipThinking key="t" />,
      <ChipStreaming key="s" text="hello" />,
      <ChipLookingItUp key="l" />,
      <ChipLoadingScreen key="ld" />,
      <ChipRendering key="r" progress={42} />,
      <ChipGeneratingClips key="g" />,
      <ChipPersonalizing key="p" />,
    ]
    for (const node of states) {
      const { container, unmount } = render(node)
      expect(container.querySelector('img[src^="/chip/"]')).toBeInTheDocument()
      unmount()
    }
  })

  it('ChipStreaming shows the streamed text', () => {
    const { getByText } = render(<ChipStreaming text="token by token" />)
    expect(getByText('token by token')).toBeInTheDocument()
  })

  it('ChipRendering clamps progress and reports it via progressbar role', () => {
    const { getByRole } = render(<ChipRendering progress={150} />)
    expect(getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100')
  })
})
