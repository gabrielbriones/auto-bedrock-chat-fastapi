// Shell-level copy: layouts, error states and navigation. FR-DS-011 / NFR-I18N-001 — every
// user-facing string is a typed constant here, never inline in JSX.
export const SHELL = {
  skipToContent: 'Skip to main content',
  mainLandmark: 'Main content',

  error: {
    title: 'Something went wrong',
    routeDescription: 'This view failed to load. The rest of the application is still available.',
    overlayDescription: 'This panel failed to load. Close it and try again.',
    appDescription: 'The application failed to start.',
    retry: 'Retry',
    reference: (reference: string) => `Reference: ${reference}`,
  },

  notFound: {
    title: 'Page not found',
    description: 'The address you followed does not match any view.',
    action: 'Back to chat',
  },

  loading: 'Loading...',

  offline: 'You are offline. Reconnection is paused until your connection returns.',

  confirmations: {
    cancel: 'Cancel',
    confirm: 'Confirm',
    submit: 'OK',
  },

  admin: {
    landmark: 'Admin',
    navigation: 'Menu',
    feedbackQueue: 'Feedback queue',
    reviewed: 'Reviewed',
    stats: 'Stats',
    knowledge: 'Knowledge base',
    kbSources: 'KB sources',
    usage: 'Usage',
    openDashboard: 'Dashboard',
    backToChat: 'Back to chat',
    // One line under each section title, so a reviewer landing on a view knows what it is for.
    descriptions: {
      feedbackQueue: 'Feedback waiting for a reviewer decision.',
      reviewed: 'Decisions already made, with rollback and cleanup.',
      stats: 'How feedback and reviews are trending.',
      knowledge: 'Browse, edit and curate the documents the assistant retrieves from.',
      kbSources: 'Crawl the web or upload files into the knowledge base.',
      usage: 'Token consumption by model, user and day.',
    },
  },

  chat: {
    transcript: 'Transcript',
  },
} as const
