import { useEffect } from 'react'
import { ButtonLink, EmptyState, IconArrowRight } from '../components/ui'

export default function NotFound() {
  useEffect(() => {
    document.title = 'Page not found · QuantFolio'
  }, [])
  return (
    <div className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
      <h1 className="sr-only">Page not found</h1>
      <EmptyState
        title="There is no page at this address"
        action={
          <ButtonLink to="/" iconRight={<IconArrowRight />}>
            Back to the overview
          </ButtonLink>
        }
      >
        Check the link, or start from the overview, the Portfolio Lab or your run history.
      </EmptyState>
    </div>
  )
}
