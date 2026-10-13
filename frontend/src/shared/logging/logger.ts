export type LogContext = Record<string, unknown>;

// STD-001 §6: interface for ports. The console adapter is the only implementation allowed
// to call console.* directly (NFR-OBS-004); everything else logs through this port.
export interface Logger {
  debug(message: string, context?: LogContext): void;
  info(message: string, context?: LogContext): void;
  warn(message: string, context?: LogContext): void;
  error(message: string, context?: LogContext): void;
}
