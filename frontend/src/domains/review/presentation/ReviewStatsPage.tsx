import { useEffect } from 'react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { ErrorState } from '@/components/ui/composed/error-state'
import { LoadingState } from '@/components/ui/composed/loading-state'
import { REVIEW_COPY } from '@/shared/copy/review'

import { StatCard } from '@/domains/review/presentation/StatCard'
import { TopTagsChart } from '@/domains/review/presentation/TopTagsChart'

// FR-REV-019: loaded on mount and revalidated whenever the tab regains focus, so a dashboard left
// open all day does not silently go stale.
function useStatsLifecycle() {
  const { reviews } = useContainer()
  const snapshot = useContainerStore('reviews')

  useEffect(() => {
    const controller = new AbortController()
    void reviews.loadStats(controller.signal)
    return () => controller.abort()
  }, [reviews])

  useEffect(() => {
    let controller: AbortController | null = null

    const onFocus = () => {
      controller?.abort()
      controller = new AbortController()
      void reviews.loadStats(controller.signal)
    }

    window.addEventListener('focus', onFocus)
    return () => {
      controller?.abort()
      window.removeEventListener('focus', onFocus)
    }
  }, [reviews])

  return { reviews, snapshot }
}

export function ReviewStatsPage() {
  const { reviews, snapshot } = useStatsLifecycle()

  if (snapshot.statsStatus === 'error') {
    return (
      <ErrorState
        description={REVIEW_COPY.stats.loadError}
        onRetry={() => {
          void reviews.loadStats(new AbortController().signal)
        }}
      />
    )
  }

  if (snapshot.stats === null) {
    return <LoadingState label={REVIEW_COPY.stats.title} />
  }

  const stats = snapshot.stats

  return (
    <section className="grid gap-6 p-4">
      <h1 className="text-xl font-semibold">{REVIEW_COPY.stats.title}</h1>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label={REVIEW_COPY.stats.total} value={stats.total} />
        <StatCard
          label={REVIEW_COPY.stats.pendingReview}
          value={stats.pendingReview}
          {...(stats.oldestPendingHours === null
            ? {}
            : { hint: REVIEW_COPY.stats.oldestPending(stats.oldestPendingHours) })}
        />
        <StatCard label={REVIEW_COPY.stats.approved} value={stats.approved} />
        <StatCard label={REVIEW_COPY.stats.rejected} value={stats.rejected} />
        <StatCard label={REVIEW_COPY.stats.positive} value={stats.positive} />
        <StatCard label={REVIEW_COPY.stats.negative} value={stats.negative} />
        <StatCard label={REVIEW_COPY.stats.withCorrection} value={stats.withCorrection} />
        <StatCard label={REVIEW_COPY.stats.integrated} value={stats.integrated} />
      </div>
      <TopTagsChart tags={stats.topTags} />
    </section>
  )
}
