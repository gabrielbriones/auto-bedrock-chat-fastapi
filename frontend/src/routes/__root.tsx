import type { ErrorComponentProps } from '@tanstack/react-router'
import { Link, Outlet, createRootRouteWithContext, useRouter } from '@tanstack/react-router';

import type { Container } from '@/app/bootstrap/container';
import { useContainer } from '@/app/bootstrap/container-context'
import { EmptyState } from '@/components/ui/composed/empty-state';
import { ErrorState } from '@/components/ui/composed/error-state';
import { AdminAccessDeniedError } from '@/domains/iam/application/admin-access-denied'
import { AccessDeniedState } from '@/domains/iam/presentation/access-denied-state'
import { SHELL } from '@/shared/copy/shell';

// DESIGN-002 §3 / FR-SHELL-028: the router's typed context carries the same Container the
// component tree resolves through `useContainer`, so `beforeLoad` guards and loaders reach their
// dependencies without a second mechanism and without a cast. The QueryClient joins it when
// QueryProvider lands.
export type RouterContext = {
  readonly container: Container;
};

function RootRouteError({ error, reset }: ErrorComponentProps) {
  const { capabilityProbe } = useContainer()
  const router = useRouter()

  if (error instanceof AdminAccessDeniedError) {
    const retry = () => {
      capabilityProbe.invalidate()
      void router.invalidate()
    }

    return <AccessDeniedState onRetry={retry} />
  }

  return <ErrorState description={SHELL.error.appDescription} onRetry={reset} />
}

// FR-SHELL-009: the outermost of the three boundaries. It renders the error itself rather than
// through a layout, since a failure here may be the layout.
export const Route = createRootRouteWithContext<RouterContext>()({
  component: () => <Outlet />,
  errorComponent: RootRouteError,
  notFoundComponent: () => (
    <EmptyState
      title={SHELL.notFound.title}
      description={SHELL.notFound.description}
      action={
        <Link to="/ui" className="text-sm underline underline-offset-4">
          {SHELL.notFound.action}
        </Link>
      }
    />
  ),
});
