export { normalizeInputType, PROMPT_INPUT_TYPES, type PromptInputType } from '@/domains/prompt-catalog/domain/input-type'
export { deriveLabel } from '@/domains/prompt-catalog/domain/label'
export {
  parseValidationRule,
  validateValue,
  type ParsedValidationRule,
  type ValidationRule,
} from '@/domains/prompt-catalog/domain/validation-rule'
export { parseDetectionRule, type DetectionRule } from '@/domains/prompt-catalog/domain/detection-rule'
export { type VariableValue } from '@/domains/prompt-catalog/domain/variable-value'
export {
  defaultBindingFor,
  bindingFromText,
  inferPromptVariable,
  parsePromptVariable,
  type ParsedPromptVariable,
  type PromptVariable,
  type PromptVariableWireConfig,
  type SelectOption,
} from '@/domains/prompt-catalog/domain/prompt-variable'
export {
  extractRequiredVariables,
  parsePresetPrompt,
  type PresetPrompt,
  type PresetPromptWireConfig,
} from '@/domains/prompt-catalog/domain/preset-prompt'
export {
  allRequiredVariableNames,
  parsePromptCatalog,
  type PromptCatalog,
} from '@/domains/prompt-catalog/domain/prompt-catalog'
export { composePreset, type ComposedPrompt, type MissingVariables } from '@/domains/prompt-catalog/domain/composed-prompt'
export { evaluatePreset, type PresetEvaluation } from '@/domains/prompt-catalog/domain/enablement'
export { detectBindings } from '@/domains/prompt-catalog/domain/detection'
export {
  parseDeepLink,
  scrubDeepLink,
  type DeepLinkIntent,
  type UrlState,
} from '@/domains/prompt-catalog/domain/deep-link'
