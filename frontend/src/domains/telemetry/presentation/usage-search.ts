export type UsageSearch = {
  readonly topLimit?: number | undefined
  readonly user?: string | undefined
  readonly from?: string | undefined
  readonly to?: string | undefined
  readonly offset: number
}

export type UsageSearchPatch = Partial<UsageSearch>
