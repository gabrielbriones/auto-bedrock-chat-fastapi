// FR-MSG-032. Whether the user can currently see the app: the tab is visible *and* its window has
// focus. A port so that trigger logic is testable without a DOM.
export interface PageAttentionPort {
  isAttended(): boolean
  subscribe(listener: (attended: boolean) => void): () => void
}
