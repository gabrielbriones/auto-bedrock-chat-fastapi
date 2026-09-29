import { useCallback, useEffect, useState, type ReactNode } from 'react'

import { ErrorState } from '@/components/ui/composed/error-state'
import { LoadingState } from '@/components/ui/composed/loading-state'
import { SHELL } from '@/shared/copy/shell'

import { isErr } from '@/shared/kernel/result'
import { HttpClient } from '@/shared/http/http-client'
import { ConsoleLogger } from '@/shared/logging/console-logger'
import type { Logger } from '@/shared/logging/logger'

import { ContainerContext } from '@/app/bootstrap/container-context'
import { createContainer, type Container } from '@/app/bootstrap/container'
import { chatBase, loadBootstrap, type BootstrapLoadError } from '@/app/bootstrap/loadBootstrap'

type BootstrapState =
  | { readonly status: 'loading' }
  | { readonly status: 'ready'; readonly container: Container }
  | { readonly status: 'error'; readonly error: BootstrapLoadError; readonly diagnosticRef: string }

export type BootstrapProviderProps = {
  readonly children: ReactNode
  readonly baseUrl?: string
  readonly httpClient?: HttpClient
  readonly logger?: Logger
}

const newDiagnosticRef = (): string =>
  typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `boot-${Date.now()}`

const FullPage = ({ children }: { readonly children: ReactNode }) => (
  <div className="flex min-h-dvh flex-col items-center justify-center">{children}</div>
)

// FR-SHELL-010 / ADR-007: fetches BC-001's bootstrap config once, and on success builds the
// Container (ADR-003) exactly once. `retry` re-runs the fetch without remounting.
function useBootstrap(client: HttpClient, log: Logger, baseUrl: string | undefined) {
  const [state, setState] = useState<BootstrapState>({ status: 'loading' })
  // Bumped by `retry` to re-run the fetch effect below without changing its other dependencies.
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false

    // The result is delivered through `setState`; nothing downstream awaits the load itself.
    void loadBootstrap(client, baseUrl ?? chatBase()).then((result) => {
      if (cancelled) {
        return
      }

      if (isErr(result)) {
        const diagnosticRef = newDiagnosticRef()
        log.error('bootstrap_load_failed', {
          diagnosticRef,
          code: result.error.code,
          ...('issues' in result.error ? { issues: result.error.issues } : {}),
        })
        setState({ status: 'error', error: result.error, diagnosticRef })
        return
      }

      setState({ status: 'ready', container: createContainer(result.value, { httpClient: client, logger: log }) })
    })

    return () => {
      cancelled = true
    }
  }, [client, log, baseUrl, attempt])

  const retry = useCallback(() => {
    setState({ status: 'loading' })
    setAttempt((previous) => previous + 1)
  }, [])

  return { state, retry }
}

// Renders nothing but a loading state until the configuration has arrived. A failure shows a
// full-page error with a retry action and a diagnostic reference — never a blank page.
export function BootstrapProvider({ children, baseUrl, httpClient, logger }: BootstrapProviderProps) {
  // Constructed once per mount (ADR-003) regardless of re-render; a prop override lets tests
  // substitute fakes with no `fetch` and no `vi.mock`.
  const [client] = useState<HttpClient>(() => httpClient ?? new HttpClient())
  const [log] = useState<Logger>(() => logger ?? new ConsoleLogger())
  const { state, retry } = useBootstrap(client, log, baseUrl)

  if (state.status === 'loading') {
    return (
      <FullPage>
        <LoadingState className="w-64" rows={2} />
      </FullPage>
    )
  }

  if (state.status === 'error') {
    return (
      <FullPage>
        <ErrorState
          title={state.error.title}
          description={SHELL.error.appDescription}
          reference={state.diagnosticRef}
          onRetry={retry}
        />
      </FullPage>
    )
  }

  return <ContainerContext.Provider value={state.container}>{children}</ContainerContext.Provider>
}
