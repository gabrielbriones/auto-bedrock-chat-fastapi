import type {
  ConfirmRequest,
  ConfirmationPort,
  PromptRequest,
} from '@/shared/ports/confirmation-port'

export type ScriptedAnswer = boolean | string | null

// STD-002 §4: a test scripts the answers a user would give, in order, and asserts on what was
// asked. Running out of answers is a test-authoring error, so it throws rather than guessing.
export class ScriptedConfirmationPort implements ConfirmationPort {
  readonly asked: (ConfirmRequest | PromptRequest)[] = []
  readonly #answers: ScriptedAnswer[]

  constructor(answers: readonly ScriptedAnswer[] = []) {
    this.#answers = [...answers]
  }

  confirm(request: ConfirmRequest): Promise<boolean> {
    this.asked.push(request)

    return Promise.resolve(this.#next(request.title) === true)
  }

  prompt(request: PromptRequest): Promise<string | null> {
    this.asked.push(request)
    const answer = this.#next(request.title)

    return Promise.resolve(typeof answer === 'string' ? answer : null)
  }

  #next(title: string): ScriptedAnswer {
    if (this.#answers.length === 0) {
      throw new Error(`ScriptedConfirmationPort has no scripted answer for "${title}"`)
    }

    return this.#answers.shift() as ScriptedAnswer
  }
}
