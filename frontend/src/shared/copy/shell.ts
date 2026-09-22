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

  loading: 'Loading…',

  offline: 'You are offline. Reconnection is paused until your connection returns.',

  confirmations: {
    cancel: 'Cancel',
    confirm: 'Confirm',
    submit: 'OK',
  },

  admin: {
    landmark: 'Admin',
    navigation: 'Admin sections',
    feedbackQueue: 'Feedback queue',
    reviewed: 'Reviewed',
    stats: 'Stats',
    knowledge: 'Knowledge base',
    usage: 'Usage',
    openDashboard: 'Admin dashboard',
    backToChat: 'Back to chat',
  },

  chat: {
    transcript: 'Transcript',
  },
} as const
