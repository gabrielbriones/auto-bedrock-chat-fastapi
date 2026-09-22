export const MAIN_SCROLL_CONTAINER_ID = 'main-content'

// FR-DS-008/FR-SHELL-018: the shell's <main> is the application's only scroll container, so
// anything that needs to observe or drive scrolling asks for that element rather than creating a
// second scroller of its own. Resolved on demand rather than held in React state: the element
// outlives every component that reads it, and writing to `scrollTop` is a DOM command, not a
// mutation of rendered state.
export const shellScrollContainer = (): HTMLElement | null =>
  document.getElementById(MAIN_SCROLL_CONTAINER_ID)
