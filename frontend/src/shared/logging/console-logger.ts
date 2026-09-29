import type { LogContext, Logger } from '@/shared/logging/logger';

type ConsoleLevel = 'debug' | 'info' | 'warn' | 'error';

export class ConsoleLogger implements Logger {
  debug(message: string, context?: LogContext): void {
    this.log('debug', message, context);
  }

  info(message: string, context?: LogContext): void {
    this.log('info', message, context);
  }

  warn(message: string, context?: LogContext): void {
    this.log('warn', message, context);
  }

  error(message: string, context?: LogContext): void {
    this.log('error', message, context);
  }

  // Sole permitted console.* usage in the codebase (NFR-OBS-004, FR-TOOL-014).
  private log(level: ConsoleLevel, message: string, context?: LogContext): void {
    if (context !== undefined) {
      console[level](message, context);
    } else {
      console[level](message);
    }
  }
}
