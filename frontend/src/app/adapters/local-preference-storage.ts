import type { PreferenceStoragePort } from '@/shared/ports/preference-storage-port'

export class LocalPreferenceStorage implements PreferenceStoragePort {
  read(key: string): string | null {
    try {
      return window.localStorage.getItem(key)
    } catch {
      return null
    }
  }

  write(key: string, value: string): void {
    try {
      window.localStorage.setItem(key, value)
    } catch {
      // The preference lasts for this page only.
    }
  }
}
