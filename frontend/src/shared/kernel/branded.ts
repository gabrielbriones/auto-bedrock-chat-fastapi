declare const brand: unique symbol;

export type Brand<Value, Name extends string> = Value & {
  readonly [brand]: Name;
};

export const createIdFactory = <Name extends string>() =>
  (value: string): Brand<string, Name> => value as Brand<string, Name>;

export type ConversationId = Brand<string, 'ConversationId'>;
export type DomainEventId = Brand<string, 'DomainEventId'>;
export type FeedbackEntryId = Brand<string, 'FeedbackEntryId'>;
export type KbDocumentId = Brand<string, 'KbDocumentId'>;
export type MessageId = Brand<string, 'MessageId'>;
export type TurnId = Brand<string, 'TurnId'>;
export type UserId = Brand<string, 'UserId'>;

export const conversationId = createIdFactory<'ConversationId'>();
export const domainEventId = createIdFactory<'DomainEventId'>();
export const feedbackEntryId = createIdFactory<'FeedbackEntryId'>();
export const kbDocumentId = createIdFactory<'KbDocumentId'>();
export const messageId = createIdFactory<'MessageId'>();
export const turnId = createIdFactory<'TurnId'>();
export const userId = createIdFactory<'UserId'>();