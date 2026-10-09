import { ButtonLink, EmptyState, IconArrowRight } from '../components/ui'

export default function NotFound() {
  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6 sm:py-12">
      <h1 className="sr-only">Page not found</h1>
      <EmptyState
        title="There is no page at this address"
        action={
          <ButtonLink to="/dashboard" iconRight={<IconArrowRight />}>
            Go to the dashboard
          </ButtonLink>
        }
      >
        Check the link, or start from the dashboard, the Portfolio Lab or your run history.
      </EmptyState>
    </div>
  )
}
