import { useEffect, useState } from 'react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { MESSAGING_COPY } from '@/shared/copy/messaging'
import { composerAvailability } from '@/domains/messaging/presentation/composer-policy'
import { allRequiredVariableNames } from '@/domains/prompt-catalog/domain/public'
import { PresetPromptBar } from '@/domains/prompt-catalog/presentation/PresetPromptBar'
import { PresetVariablePanel } from '@/domains/prompt-catalog/presentation/PresetVariablePanel'

// SPEC-013 §5 (Task 23 Phase 2): the one place `prompt-catalog` and `messaging` meet, alongside
// `ConversationView` — neither context imports the other (DESIGN-002 §3). Self-contained so
// `ConversationView` only has to render it as `ChatPanel`'s `presetArea`.
export function PromptCatalogArea() {
  const { bootstrap, promptCatalog } = useContainer()
  const { inputEnabled } = useContainerStore('identity')
  const { connection, awaitingResponse } = useContainerStore('chatSession')
  const { bindings, detected } = useContainerStore('promptCatalog')
  const [showValidationErrors, setShowValidationErrors] = useState(false)

  const availability = composerAvailability(
    {
      inputEnabled,
      connected: connection.kind === 'connected',
      awaitingResponse,
      lockWhileResponding: bootstrap.lockInputWhileResponding,
    },
    MESSAGING_COPY.composer.disabled,
  )

  // FR-PROMPT-008a/016: retried on every readiness change (mount, reconnect) — the store itself
  // guarantees at most one send no matter how many times this fires.
  useEffect(() => {
    promptCatalog.tryAutoSend(availability.enabled)
  }, [promptCatalog, availability.enabled])

  return (
    <div className="mx-auto flex w-full max-w-prose min-w-0 flex-col gap-4 text-left">
      <PresetVariablePanel
        variableNames={allRequiredVariableNames(promptCatalog.catalog)}
        variables={promptCatalog.catalog.variables}
        bindings={bindings}
        detected={detected}
        showValidationErrors={showValidationErrors}
        onBindingChange={(name, value) => promptCatalog.bindVariable(name, value)}
      />
      <PresetPromptBar
        catalog={promptCatalog.catalog}
        bindings={bindings}
        locked={!availability.enabled}
        lockedReason={availability.reason ?? ''}
        onActivate={(presetId) => {
          setShowValidationErrors(true)
          promptCatalog.activatePreset(presetId)
        }}
      />
    </div>
  )
}
