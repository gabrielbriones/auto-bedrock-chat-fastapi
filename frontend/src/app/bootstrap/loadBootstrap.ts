import { andThen, type Result } from '@/shared/kernel/result';
import type { Problem } from '@/shared/http/exception';
import type { HttpClient } from '@/shared/http/http-client';

import { toChatBootstrap, type BootstrapParseError } from '@/app/bootstrap/bootstrap-config.dto';
import type { ChatBootstrap } from '@/app/bootstrap/chat-bootstrap';

const DEFAULT_CHAT_BASE = '/bedrock-chat';

export type BootstrapLoadError = Problem | BootstrapParseError;

// STD-001 §9: the only build-time value; every other path comes from the resolved bootstrap.
// Requests stay same-origin (no absolute backend origin is ever configured client-side).
export const chatBase = (): string => {
  const configured = import.meta.env.VITE_CHAT_BASE as string | undefined;
  return configured !== undefined && configured.length > 0 ? configured : DEFAULT_CHAT_BASE;
};

// BC-001 (CONTRACT-001 §6): fetches and validates the bootstrap config once, before the app
// renders anything but the loading state (FR-SHELL-010). Never throws.
export const loadBootstrap = async (
  httpClient: HttpClient,
  baseUrl: string = chatBase(),
): Promise<Result<ChatBootstrap, BootstrapLoadError>> => {
  const response = await httpClient.request<unknown>(`${baseUrl}/config`);
  return andThen(response, toChatBootstrap);
};
