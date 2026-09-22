import { ConsoleLogger } from '@/shared/logging/console-logger'
import type { Logger } from '@/shared/logging/logger'
import { HttpClient } from '@/shared/http/http-client'
import { SystemClock, type Clock } from '@/shared/kernel/instant'
import { conversationId } from '@/shared/kernel/branded'
import type { ConnectivityPort } from '@/shared/ports/connectivity-port'
import type { NotificationPort } from '@/shared/ports/notification-port'
import { MessageBus } from '@/shared/ws/message-bus'
import { SocketClient, type SocketFactory } from '@/shared/ws/socket-client'

import { BrowserConnectivityPort } from '@/app/adapters/browser-connectivity-port'
import { ConfirmationController } from '@/app/adapters/confirmation-controller'
import { SonnerNotificationPort } from '@/app/adapters/sonner-notification-port'
import { browserUrlNavigator } from '@/app/browser-location'
import type { ChatBootstrap } from '@/app/bootstrap/chat-bootstrap'
import { toAuthPolicy, type AuthPolicy } from '@/domains/iam/domain/auth-policy'
import { IdentityStore } from '@/domains/iam/application/identity.store'
import type {
  AuthGateway,
  CapabilityProbe,
  SsoGateway,
} from '@/domains/iam/application/ports'
import { CapabilityHttpProbe } from '@/domains/iam/infrastructure/capability-http.probe'
import { SsoHttpGateway, type LoginNavigation } from '@/domains/iam/infrastructure/sso-http.gateway'
import { WsAuthGateway } from '@/domains/iam/infrastructure/ws-auth.gateway'
import { ChatSessionStore } from '@/domains/messaging/application/chat-session.store'
import type { MessagingGateway } from '@/domains/messaging/application/ports'
import { resolveSocketUrl } from '@/domains/messaging/domain/socket-url'
import { WsMessagingGateway } from '@/domains/messaging/infrastructure/ws-messaging.gateway'
import { ConversationStore } from '@/domains/conversation/application/conversation.store'
import type { ConversationGateway } from '@/domains/conversation/application/ports'
import { IntervalPendingTurnScheduler } from '@/domains/conversation/infrastructure/interval-pending-turn.scheduler'
import { PromptCatalogStore } from '@/domains/prompt-catalog/application/prompt-catalog.store'
import type { UrlNavigator } from '@/domains/prompt-catalog/application/ports'
import { parsePromptCatalog } from '@/domains/prompt-catalog/domain/public'
import { WsConversationGateway } from '@/domains/conversation/infrastructure/ws-conversation.gateway'
import { ModelConfigStore } from '@/domains/model-config/application/model-config.store'
import type { ConfigurationGateway } from '@/domains/model-config/application/ports'
import { toConfigurationProfile } from '@/domains/model-config/domain/configuration-profile'
import { buildModelCatalog } from '@/domains/model-config/domain/model-catalog'
import { WsModelConfigGateway } from '@/domains/model-config/infrastructure/ws-model-config.gateway'
import { FeedbackStore } from '@/domains/feedback/application/feedback.store'
import type { FeedbackGateway, SubmittedFeedbackLog } from '@/domains/feedback/application/ports'
import { SessionStorageSubmittedFeedbackLog } from '@/domains/feedback/infrastructure/session-storage-submitted-feedback-log'
import { WsFeedbackGateway } from '@/domains/feedback/infrastructure/ws-feedback.gateway'
import type { ReviewGateway } from '@/domains/review/application/ports'
import { HttpReviewGateway } from '@/domains/review/infrastructure/http-review.gateway'
import { ReviewStore } from '@/domains/review/application/review.store'
import type { KnowledgeGateway } from '@/domains/knowledge/application/ports'
import { KnowledgeStore } from '@/domains/knowledge/application/knowledge.store'
import { HttpKnowledgeGateway } from '@/domains/knowledge/infrastructure/http-knowledge.gateway'
import type { TelemetryGateway } from '@/domains/telemetry/application/ports'
import { TelemetryStore } from '@/domains/telemetry/application/telemetry.store'
import { HttpTelemetryGateway } from '@/domains/telemetry/infrastructure/http-telemetry.gateway'

// ADR-003 / DESIGN-002 §3: the single typed record of every adapter/port the app owns, built
// once after bootstrap resolves and reached only through `useContainer` — never a service
// locator or module-level singleton.
export type Container = {
  readonly httpClient: HttpClient
  readonly logger: Logger
  readonly clock: Clock
  readonly notifications: NotificationPort
  // The concrete controller, not just the port: the global host subscribes to it.
  readonly confirmations: ConfirmationController
  readonly connectivity: ConnectivityPort
  readonly capabilityProbe: CapabilityProbe
  // ADR-004: one multiplexed socket shared by `iam` and `messaging`. Constructed here but not
  // connected — `SocketProvider` owns opening and disposing it.
  readonly socket: SocketClient
  readonly messageBus: MessageBus
  readonly authGateway: AuthGateway
  readonly ssoGateway: SsoGateway
  readonly authPolicy: AuthPolicy
  readonly identity: IdentityStore
  readonly messagingGateway: MessagingGateway
  readonly chatSession: ChatSessionStore
  readonly conversationGateway: ConversationGateway
  readonly conversations: ConversationStore
  readonly promptCatalog: PromptCatalogStore
  readonly configurationGateway: ConfigurationGateway
  readonly modelConfig: ModelConfigStore
  readonly feedbackGateway: FeedbackGateway
  readonly feedbackLog: SubmittedFeedbackLog
  readonly feedback: FeedbackStore
  // Admin-only, and HTTP rather than socket-backed (SPEC-016 §3): the review surface is a set of
  // request/response reads, not a live session.
  readonly reviewGateway: ReviewGateway
  readonly reviews: ReviewStore
  readonly knowledgeGateway: KnowledgeGateway
  readonly knowledge: KnowledgeStore
  readonly telemetryGateway: TelemetryGateway
  readonly telemetry: TelemetryStore
  readonly bootstrap: ChatBootstrap
}

export type ContainerDependencies = {
  readonly httpClient?: HttpClient
  readonly logger?: Logger
  readonly clock?: Clock
  readonly notifications?: NotificationPort
  readonly confirmations?: ConfirmationController
  readonly connectivity?: ConnectivityPort
  readonly capabilityProbe?: CapabilityProbe
  readonly socketFactory?: SocketFactory
  readonly navigation?: LoginNavigation
  readonly urlNavigator?: UrlNavigator
  /** NFR-SEC-003: the origin server-supplied redirects are validated against. */
  readonly origin?: string
  /** FR-MSG-001: the page the websocket URL is resolved against. */
  readonly pageUrl?: string
}

// NFR-SEC-003. Fails closed: with no document there is no origin to trust, so every
// server-supplied redirect is rejected rather than waved through.
const documentOrigin = (): string => (typeof location === 'undefined' ? '' : location.origin)

const documentUrl = (): string => (typeof location === 'undefined' ? '' : location.href)

const browserNavigation: LoginNavigation = {
  assign(url) {
    window.location.assign(url)
  },
}

// The single socket the application lives on (FR-IAM-010), with the bus already subscribed to it
// so no frame can arrive before there is somewhere to deliver it.
const createTransport = (
  bootstrap: ChatBootstrap,
  clock: Clock,
  connectivity: ConnectivityPort,
  logger: Logger,
  socketFactory: ContainerDependencies['socketFactory'],
  pageUrl: string,
) => {
  const messageBus = new MessageBus(logger)
  const socket = new SocketClient({
    clock,
    connectivity,
    // FR-MSG-001: the payload may carry a path or an absolute URL; the scheme always follows the
    // page's security context.
    url: resolveSocketUrl(bootstrap.websocketUrl, pageUrl),
    ...(socketFactory === undefined ? {} : { socketFactory }),
  })
  socket.onFrame((frame) => {
    messageBus.receive(frame)
  })

  return { socket, messageBus }
}

const createIdentity = (
  bootstrap: ChatBootstrap,
  authPolicy: AuthPolicy,
  authGateway: AuthGateway,
  ssoGateway: SsoGateway,
  socket: SocketClient,
): IdentityStore =>
  new IdentityStore({
    policy: authPolicy,
    authGateway,
    ssoGateway,
    connection: socket,
    initial: {
      policy: authPolicy,
      ssoAuthenticated: bootstrap.ssoAuthenticated,
      ssoUserDisplay: bootstrap.ssoUserDisplay,
    },
  })

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

// The two socket-backed stores the chat view lives on, built together because they share one
// socket and one clock (ADR-004).
const createChatStores = (
  bootstrap: ChatBootstrap,
  socket: SocketClient,
  messageBus: MessageBus,
  parts: {
    readonly clock: Clock
    readonly logger: Logger
    readonly notifications: NotificationPort
    readonly confirmations: ConfirmationController
  },
) => {
  const conversationGateway = new WsConversationGateway(socket, messageBus)
  const conversations = new ConversationStore({
    gateway: conversationGateway,
    connection: socket,
    confirmations: parts.confirmations,
    notifications: parts.notifications,
    scheduler: new IntervalPendingTurnScheduler(),
    clock: parts.clock,
    logger: parts.logger,
    persistenceEnabled: bootstrap.conversationPersistenceEnabled,
  })
  const messagingGateway = new WsMessagingGateway(
    socket,
    messageBus,
    (id) => conversations.acceptsLoadedConversation(conversationId(id)),
  )

  return {
    messagingGateway,
    conversationGateway,
    chatSession: new ChatSessionStore({
      gateway: messagingGateway,
      connection: socket,
      clock: parts.clock,
      logger: parts.logger,
    }),
    conversations,
  }
}

// The auth pieces built together because they share the socket, the origin and `bootstrap`.
const createAuth = (
  bootstrap: ChatBootstrap,
  socket: SocketClient,
  messageBus: MessageBus,
  httpClient: HttpClient,
  logger: Logger,
  deps: Pick<ContainerDependencies, 'origin' | 'navigation'>,
) => {
  const authPolicy = toAuthPolicy(bootstrap)
  const authGateway = new WsAuthGateway(socket, messageBus, logger, deps.origin ?? documentOrigin())
  const ssoGateway = new SsoHttpGateway(
    bootstrap.ssoLoginUrl,
    bootstrap.ssoLogoutUrl,
    httpClient,
    deps.navigation ?? browserNavigation,
  )

  return { authPolicy, authGateway, ssoGateway }
}

const createModelConfig = (
  bootstrap: ChatBootstrap,
  socket: SocketClient,
  messageBus: MessageBus,
  notifications: NotificationPort,
) => {
  const configurationGateway = new WsModelConfigGateway(socket, messageBus)
  const catalog = buildModelCatalog(bootstrap.availableModels, bootstrap.availableModelGroups)
  const modelConfig = new ModelConfigStore({
    profile: toConfigurationProfile(
      { model_id: bootstrap.modelId, ...bootstrap.overrideDefaults },
      bootstrap.allowedDynamicOverrides,
      catalog,
    ),
    gateway: configurationGateway,
    connection: socket,
    notifications,
  })

  return { configurationGateway, modelConfig }
}

const createFeedback = (socket: SocketClient, messageBus: MessageBus) => {
  const feedbackGateway = new WsFeedbackGateway(socket, messageBus)
  const feedbackLog = new SessionStorageSubmittedFeedbackLog()
  const feedback = new FeedbackStore({ gateway: feedbackGateway, submittedLog: feedbackLog })

  return { feedbackGateway, feedbackLog, feedback }
}

const createReviews = (
  bootstrap: ChatBootstrap,
  httpClient: HttpClient,
  logger: Logger,
  confirmations: ConfirmationController,
  notifications: NotificationPort,
) => {
  const reviewGateway = new HttpReviewGateway(bootstrap.adminPrefix, httpClient, logger)
  const reviews = new ReviewStore({ gateway: reviewGateway, confirmations, notifications, logger })

  return { reviewGateway, reviews }
}

// Constructible from fakes with no DOM (STD-002 §4): tests pass `deps` to substitute any
// port without `vi.mock`.
export const createContainer = (
  bootstrap: ChatBootstrap,
  deps: ContainerDependencies = {},
): Container => {
  const clock = deps.clock ?? new SystemClock()
  const httpClient = deps.httpClient ?? new HttpClient()
  const logger = deps.logger ?? new ConsoleLogger()
  const connectivity = deps.connectivity ?? new BrowserConnectivityPort()
  const notifications = deps.notifications ?? new SonnerNotificationPort(clock)
  const confirmations = deps.confirmations ?? new ConfirmationController()
  const { socket, messageBus } = createTransport(bootstrap, clock, connectivity, logger, deps.socketFactory, deps.pageUrl ?? documentUrl())
  const { authPolicy, authGateway, ssoGateway } = createAuth(bootstrap, socket, messageBus, httpClient, logger, deps)
  const chatStores = createChatStores(bootstrap, socket, messageBus, {
    clock,
    logger,
    notifications,
    confirmations,
  })
  const modelConfig = createModelConfig(bootstrap, socket, messageBus, notifications)
  const feedback = createFeedback(socket, messageBus)
  const reviews = createReviews(bootstrap, httpClient, logger, confirmations, notifications)
  const knowledgeGateway = new HttpKnowledgeGateway(bootstrap.adminPrefix, httpClient, logger)
  const knowledge = new KnowledgeStore({ gateway: knowledgeGateway, confirmations, notifications, logger })
  const telemetryGateway = new HttpTelemetryGateway(bootstrap.adminPrefix, httpClient, logger); const telemetry = new TelemetryStore({ gateway: telemetryGateway })

  return {
    httpClient,
    logger,
    clock,
    notifications,
    confirmations,
    connectivity,
    capabilityProbe: deps.capabilityProbe ?? new CapabilityHttpProbe(bootstrap.adminPrefix, httpClient, logger),
    socket,
    messageBus,
    authGateway,
    ssoGateway,
    authPolicy,
    identity: createIdentity(bootstrap, authPolicy, authGateway, ssoGateway, socket),
    ...chatStores,
    promptCatalog: createPromptCatalog(bootstrap, chatStores.chatSession, deps.urlNavigator ?? browserUrlNavigator, logger),
    ...modelConfig,
    ...feedback,
    ...reviews,
    knowledgeGateway,
    knowledge,
    // eslint-disable-next-line max-lines
    telemetryGateway,
    telemetry,
    bootstrap,
  }
}
