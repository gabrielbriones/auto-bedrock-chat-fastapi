import { describe, expect, it } from 'vitest'
import { renderHook } from '@testing-library/react'

import { useContainer } from '@/app/bootstrap/container-context'

describe('useContainer', () => {
  it('throws when used outside a BootstrapProvider', () => {
    expect(() => renderHook(() => useContainer())).toThrow(
      'useContainer must be used within a BootstrapProvider',
    )
  })
})
