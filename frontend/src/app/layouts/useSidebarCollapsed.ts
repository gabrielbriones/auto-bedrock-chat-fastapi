import { useCallback, useEffect, useState } from 'react'

const STORAGE_KEY = 'chat.sidebar.collapsed'

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

export function useSidebarCollapsed() {
  const [collapsed, setCollapsed] = useState(readCollapsed)

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, String(collapsed))
    } catch {
      // The rail remains usable for this session when storage is unavailable.
    }
  }, [collapsed])

  const toggle = useCallback(() => { setCollapsed((current) => !current) }, [])

  return [collapsed, toggle] as const
}
