import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TrimFilmstrip } from './TrimFilmstrip'
import { clampTrim } from './trim'

describe('clampTrim', () => {
  it('keeps the start handle below end minus the minimum window', () => {
    // start can't cross end (min 0.5s window)
    expect(clampTrim('start', 19, 0, 20, 20)).toEqual({ start: 19, end: 20 })
    expect(clampTrim('start', 19.9, 0, 20, 20)).toEqual({ start: 19.5, end: 20 })
  })

  it('floors the start handle at 0', () => {
    expect(clampTrim('start', -5, 5, 20, 20)).toEqual({ start: 0, end: 20 })
  })

  it('keeps the end handle above start plus the minimum window', () => {
    expect(clampTrim('end', 0.1, 5, 20, 20)).toEqual({ start: 5, end: 5.5 })
  })

  it('caps the end handle at the duration', () => {
    expect(clampTrim('end', 99, 5, 20, 20)).toEqual({ start: 5, end: 20 })
  })
})

describe('TrimFilmstrip', () => {
  it('renders two trim handles and a live selected-duration readout', () => {
    render(<TrimFilmstrip duration={30} trimStart={5} trimEnd={20} onChange={vi.fn()} />)
    expect(screen.getByRole('slider', { name: 'Trim start' })).toBeInTheDocument()
    expect(screen.getByRole('slider', { name: 'Trim end' })).toBeInTheDocument()
    // 20 - 5 = 15.0s selected
    expect(screen.getByText('15.0s selected')).toBeInTheDocument()
  })
})
