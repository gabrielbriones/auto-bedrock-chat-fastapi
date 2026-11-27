import type { Logger } from '@/shared/logging/logger'

import type { ChatBootstrap } from '@/app/bootstrap/chat-bootstrap'
import { SkillDeepLink } from '@/app/chat/skill-deep-link'
import type { ChatSessionStore } from '@/domains/messaging/application/chat-session.store'
import { PromptCatalogStore } from '@/domains/prompt-catalog/application/prompt-catalog.store'
import type { UrlNavigator } from '@/domains/prompt-catalog/application/ports'
import { parsePromptCatalog } from '@/domains/prompt-catalog/domain/public'

// SPEC-013 §3: the anti-corruption boundary — the prompt-catalog store hands over a composed
// prompt and never touches the messaging store beyond calling `send`.
const createPromptCatalog = (
  bootstrap: ChatBootstrap,
  chatSession: ChatSessionStore,
  urlNavigator: UrlNavigator,
  logger: Logger,
): PromptCatalogStore => {
  const catalog = parsePromptCatalog(bootstrap.presetPrompts, bootstrap.variables)

  // FR-PROMPT-004a: parse-time diagnostics (an uncompilable `validate` regex) are data until now —
  // this is the one place they're actually logged, once each, at startup.
  for (const diagnostic of catalog.diagnostics) {
    logger.warn('prompt_catalog_diagnostic', { message: diagnostic })
  }

  return new PromptCatalogStore({
    catalog,
    sink: { submit: (composed) => { chatSession.send(composed.text) } },
    urlNavigator,
    logger,
  })
}

// Both deep-link forms share the address bar; the skill one falls back to the preset one.
export const createDeepLinks = (
  bootstrap: ChatBootstrap,
  chatSession: ChatSessionStore,
  urlNavigator: UrlNavigator,
  logger: Logger,
) => {
  const promptCatalog = createPromptCatalog(bootstrap, chatSession, urlNavigator, logger)
  const send = (text: string) => {
    chatSession.send(text)
  }

  return { promptCatalog, skillDeepLink: new SkillDeepLink({ urlNavigator, send, legacy: promptCatalog, logger }) }
}
