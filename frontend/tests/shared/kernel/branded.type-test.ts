import { conversationId, type MessageId } from '@/shared/kernel/branded';

const aConversationId = conversationId('conversation-1');

const acceptsMessageId = (value: MessageId): MessageId => value;

// @ts-expect-error A ConversationId is not interchangeable with a MessageId.
acceptsMessageId(aConversationId);