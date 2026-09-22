import { describe, expect, it } from 'vitest';

import { domainEventId } from '@/shared/kernel/branded';
import { InMemoryDomainEventPublisher, type DomainEvent } from '@/shared/kernel/domain-event';
import { Instant } from '@/shared/kernel/instant';
import { isOk } from '@/shared/kernel/result';

describe('InMemoryDomainEventPublisher', () => {
  it('publishes events to subscribers until they unsubscribe', () => {
    const occurredAt = Instant.fromIso('2026-08-13T10:00:00.000Z');
    if (!isOk(occurredAt)) {
      return;
    }

    const event: DomainEvent = {
      eventId: domainEventId('event-1'),
      occurredAt: occurredAt.value,
      type: 'conversation.created',
    };
    const publisher = new InMemoryDomainEventPublisher();
    const received: DomainEvent[] = [];
    const unsubscribe = publisher.subscribe((published) => received.push(published));

    publisher.publish(event);
    unsubscribe();
    publisher.publish(event);

    expect(received).toEqual([event]);
  });
});