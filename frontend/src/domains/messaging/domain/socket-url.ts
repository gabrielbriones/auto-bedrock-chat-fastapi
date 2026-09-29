// FR-MSG-001. The bootstrap payload may carry a path (`/bedrock-chat/ws`), an origin-relative URL
// or an absolute one; all three resolve against the page, and the scheme always follows the page's
// security context rather than whatever the server happened to serialise. No token is ever added.
export const resolveSocketUrl = (websocketUrl: string, pageUrl: string): string => {
  let page: URL
  try {
    page = new URL(pageUrl)
  } catch {
    // No document to resolve against — a headless test runner, never a browser. The value is
    // returned untouched rather than resolved against an invented origin.
    return websocketUrl
  }

  let resolved: URL
  try {
    resolved = new URL(websocketUrl, page)
  } catch {
    return websocketUrl
  }

  resolved.protocol = page.protocol === 'https:' ? 'wss:' : 'ws:'

  return resolved.toString()
}
