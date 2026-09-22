// DESIGN-001 §5.1: a discriminated union matching each `PromptInputType` one-for-one, so a
// mismatched `kind` (e.g. binding a `'number'` value to a `'select'` variable) is a type error
// rather than a runtime surprise. Deliberately depends on nothing else in this context — every
// other prompt-catalog module treats a variable's bound value as this shape.
export type VariableValue =
  | { readonly kind: 'text'; readonly value: string }
  | { readonly kind: 'select'; readonly value: string }
  | { readonly kind: 'number'; readonly value: number }
  | { readonly kind: 'boolean'; readonly value: boolean }
