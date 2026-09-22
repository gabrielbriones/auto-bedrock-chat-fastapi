import { afterEach, describe, expect, it, vi } from 'vitest';

import { ConsoleLogger } from '@/shared/logging/console-logger';

describe('ConsoleLogger', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('delegates each level to the matching console method', () => {
    const debug = vi.spyOn(console, 'debug').mockImplementation(() => undefined);
    const info = vi.spyOn(console, 'info').mockImplementation(() => undefined);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);

    const logger = new ConsoleLogger();
    logger.debug('debug message');
    logger.info('info message', { requestId: 'abc' });
    logger.warn('warn message');
    logger.error('error message', { code: 'boom' });

    expect(debug).toHaveBeenCalledWith('debug message');
    expect(info).toHaveBeenCalledWith('info message', { requestId: 'abc' });
    expect(warn).toHaveBeenCalledWith('warn message');
    expect(error).toHaveBeenCalledWith('error message', { code: 'boom' });
  });

  it('omits the context argument entirely when none is given', () => {
    const info = vi.spyOn(console, 'info').mockImplementation(() => undefined);

    new ConsoleLogger().info('no context');

    expect(info).toHaveBeenCalledWith('no context');
    expect(info.mock.calls[0]).toHaveLength(1);
  });
});
