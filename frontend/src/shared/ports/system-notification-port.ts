// `unsupported` folds "no Notifications API" into the same state machine as the browser's own
// three permission values, so callers never feature-detect.
export type SystemNotificationPermission = 'granted' | 'denied' | 'default' | 'unsupported'

export type SystemNotification = {
  readonly title: string
  readonly body: string
  /** A newer notification with the same tag replaces the older one instead of stacking. */
  readonly tag: string
  /** Runs after the adapter has focused the window and closed the notification. */
  readonly onClick: () => void
}

// FR-MSG-032. OS-level notifications, separate from the in-app toast `NotificationPort`: they are
// permission-gated, may be unavailable, and are only meant for a user who is not looking.
export interface SystemNotificationPort {
  permission(): SystemNotificationPermission
  /** Resolves to the resulting permission; never rejects. */
  requestPermission(): Promise<SystemNotificationPermission>
  /** A no-op unless permission is `granted`. */
  show(notification: SystemNotification): void
}
