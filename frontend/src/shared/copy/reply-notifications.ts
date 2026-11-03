// FR-MSG-032 — OS-level reply notifications and their on/off control. FR-DS-011 / NFR-I18N-001.
export const REPLY_NOTIFICATION_COPY = {
  emptyReply: 'The assistant replied.',
  failed: (preview: string) => `Reply failed: ${preview}`,
  unreadTitle: (count: number, title: string) => `(${count}) ${title}`,

  toggle: {
    enable: 'Turn on reply notifications',
    disable: 'Turn off reply notifications',
  },

  enabled: {
    title: 'Reply notifications are on',
    description: 'You will get a notification when the assistant replies while this tab is in the background.',
  },

  blocked: {
    title: 'Notifications are blocked',
    description: 'Allow notifications for this site in your browser settings to get an alert when the assistant replies.',
  },
} as const
