import { Outlet, createFileRoute } from '@tanstack/react-router'

import { AdminLayout } from '@/app/layouts/AdminLayout'
import { AdminAccessDeniedError } from '@/domains/iam/application/admin-access-denied'

function AdminRoute() {
  const { capabilities } = Route.useRouteContext()

  return (
    <AdminLayout capabilities={capabilities}>
      <Outlet />
    </AdminLayout>
  )
}

export const Route = createFileRoute('/dashboard')({
  beforeLoad: async ({ context }) => {
    const capabilities = await context.container.capabilityProbe.probe()

    if (!capabilities.isAdmin) {
      throw new AdminAccessDeniedError()
    }

    return { capabilities }
  },
  component: AdminRoute,
})
