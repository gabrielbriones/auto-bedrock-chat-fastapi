export type ModelDescriptor = {
  readonly id: string
  readonly name: string
  readonly provider: string
  readonly supportsTemperature: boolean
  readonly maxOutputTokens: number
}
