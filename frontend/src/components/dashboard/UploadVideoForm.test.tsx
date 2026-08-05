import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { UploadQueueItem } from '@/hooks/useUploader'
import { UploadVideoForm } from './UploadVideoForm'

// The app-wide uploader is exercised in lib/uploader.test.ts; here we pin the
// form's wiring: file intake → addFiles, Upload click → start with the
// extracted association, and the queue/session-expired renderings.
const hook = {
  ready: true,
  items: [] as UploadQueueItem[],
  sessionExpired: false,
  addFiles: vi.fn((_files: File[]): string | null => null),
  start: vi.fn(),
  retryFile: vi.fn(),
  removeFile: vi.fn(),
  clearFinished: vi.fn(),
}

vi.mock('@/hooks/useUploader', () => ({
  useUploader: () => hook,
}))

function item(over: Partial<UploadQueueItem> = {}): UploadQueueItem {
  return {
    id: 'f1',
    name: 'clip.mp4',
    sizeBytes: 100,
    progressPct: 40,
    status: 'uploading',
    error: null,
    videoId: null,
    ...over,
  }
}

afterEach(() => {
  vi.clearAllMocks()
  hook.items = []
  hook.sessionExpired = false
})

describe('UploadVideoForm', () => {
  it('renders nothing when closed', () => {
    const { container } = render(<UploadVideoForm open={false} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('feeds selected files into the uploader queue', async () => {
    render(<UploadVideoForm open />)
    const input = screen.getByLabelText('Video files to upload')
    await userEvent.upload(input, new File(['x'], 'clip.mp4', { type: 'video/mp4' }))
    expect(hook.addFiles).toHaveBeenCalledTimes(1)
    expect(hook.addFiles.mock.calls[0][0][0].name).toBe('clip.mp4')
  })

  it('starts the upload with the association extracted from a pasted URL', async () => {
    hook.items = [item({ status: 'queued', progressPct: 0 })]
    render(<UploadVideoForm open />)
    await userEvent.type(
      screen.getByLabelText('Associate with a published YouTube video (optional)'),
      'https://www.youtube.com/watch?v=abc12345678',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Upload' }))
    expect(hook.start).toHaveBeenCalledWith('abc12345678')
  })

  it('nudges instead of starting when the queue is empty', async () => {
    render(<UploadVideoForm open />)
    await userEvent.click(screen.getByRole('button', { name: 'Upload' }))
    expect(hook.start).not.toHaveBeenCalled()
    expect(screen.getByText('Choose a video file to upload.')).toBeInTheDocument()
  })

  it('shows per-file progress and a Retry affordance on failure', () => {
    hook.items = [
      item(),
      item({ id: 'f2', name: 'bad.mp4', status: 'error', error: 'File exceeds 20 GB limit' }),
    ]
    render(<UploadVideoForm open />)
    expect(screen.getByText('40%')).toBeInTheDocument()
    expect(screen.getByText('File exceeds 20 GB limit')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('explains session expiry without losing the queue', () => {
    hook.sessionExpired = true
    render(<UploadVideoForm open />)
    expect(screen.getByText(/session expired mid-upload/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Sign in again' })).toHaveAttribute(
      'href',
      '/app/login',
    )
  })
})
