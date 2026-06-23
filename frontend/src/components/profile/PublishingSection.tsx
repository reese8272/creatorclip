import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader } from '@/components/ui/card'

// Issue 194 — opt into the youtube.upload write scope via incremental consent.
// The button hits the server redirect (/auth/connect-publishing) which sends the
// creator to Google's consent screen for the upload scope on top of their grant.
export function PublishingSection({ canPublish }: { canPublish: boolean }) {
  return (
    <Card>
      <CardHeader
        title="YouTube publishing"
        description="Grant AutoClip permission to upload finished clips to your channel. Optional — only requested when you turn it on, never at sign-in."
      />
      <CardBody className="flex flex-col gap-3 text-sm">
        {canPublish ? (
          <p className="text-success">
            ✓ Publishing is enabled. AutoClip can upload clips to your channel.
          </p>
        ) : (
          <>
            <p className="text-muted">
              Publishing is off. Enabling it opens Google's consent screen for upload access;
              you can revoke it any time from your Google account.
            </p>
            <p className="text-xs text-subtle">
              While our YouTube API review is pending, uploads are posted as private — you
              choose when to make each one public. AutoClip estimates fit with your audience;
              it never promises virality.
            </p>
            <Button className="w-fit" onClick={() => (window.location.href = '/auth/connect-publishing')}>
              Enable YouTube publishing
            </Button>
          </>
        )}
      </CardBody>
    </Card>
  )
}
