import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, jest } from '@jest/globals'

import type { Container } from '@/app/bootstrap/container'
import { ContainerContext } from '@/app/bootstrap/container-context'
import { useSkillsActive } from '@/app/chat/slash-commands'
import { err, ok, type Result } from '@/shared/kernel/result'
import { SystemClock } from '@/shared/kernel/instant'
import type { Problem } from '@/shared/http/exception'

import { SkillCatalogStore } from '@/domains/skills/application/skill-catalog.store'
import type { SkillCatalogEntry } from '@/domains/skills/domain/public'
import { fakeContainer } from '../bootstrap/container.fixture'

const ENTRY: SkillCatalogEntry = { name: 'triage', displayName: 'Triage', description: '', parameters: [], revision: 1 }

const render = (skillsEnabled: boolean, result: Result<readonly SkillCatalogEntry[], Problem>) => {
  const base = fakeContainer()
  const skillCatalog = new SkillCatalogStore({
    gateway: { list: async () => result },
    clock: new SystemClock(),
    logger: { debug: jest.fn(), info: jest.fn(), warn: jest.fn(), error: jest.fn() },
  })
  const container: Container = { ...base, skillCatalog, bootstrap: { ...base.bootstrap, skillsEnabled } }
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ContainerContext.Provider value={container}>{children}</ContainerContext.Provider>
  )
  const hook = renderHook(() => useSkillsActive(), { wrapper })
  return { ...hook, skillCatalog }
}

describe('useSkillsActive', () => {
  it('is off when the server has no skills', () => {
    expect(render(false, ok([ENTRY])).result.current).toBe(false)
  })

  it('hides the presets until the catalog is read, and keeps them hidden when it lists skills', async () => {
    const { result, skillCatalog } = render(true, ok([ENTRY]))
    expect(result.current).toBe(true)

    await skillCatalog.refresh()

    await waitFor(() => expect(result.current).toBe(true))
  })

  it.each([
    ['empty', ok([])],
    ['unreadable', err({ code: 'network-error' as const, title: 'offline' })],
  ])('brings the presets back when the catalog is %s', async (_case, outcome) => {
    const { result, skillCatalog } = render(true, outcome as Result<readonly SkillCatalogEntry[], Problem>)

    await skillCatalog.refresh()

    await waitFor(() => expect(result.current).toBe(false))
  })
})
