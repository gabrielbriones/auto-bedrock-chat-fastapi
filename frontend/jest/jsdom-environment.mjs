import BaseJSDOMEnvironment from '@jest/environment-jsdom-abstract'
import * as jsdom from 'jsdom'

// jest-environment-jsdom pins jsdom 26, which predates `PointerEvent` (needed by @base-ui/react's
// click handling). The abstract environment takes jsdom as a peer, so this wires in the jsdom the
// project already depends on.
export default class JSDOMEnvironment extends BaseJSDOMEnvironment {
  constructor(config, context) {
    super(config, context, jsdom)

    // jsdom is not a browser: packages must resolve their Node builds, not the `browser` condition.
    this.customExportConditions = ['']

    // The fetch and streams globals msw/node and the HTTP gateways rely on. `Blob`, `File` and
    // `FormData` are deliberately left as jsdom's own so `FileReader` keeps accepting them.
    for (const name of [
      'TextEncoder',
      'TextDecoder',
      'TextEncoderStream',
      'TextDecoderStream',
      'ReadableStream',
      'WritableStream',
      'TransformStream',
      'BroadcastChannel',
      'Headers',
      'Request',
      'Response',
      'fetch',
      'structuredClone',
    ]) {
      this.global[name] = globalThis[name]
    }
  }
}
