import type { DomainEventId } from '@/shared/kernel/branded';
import type { Instant } from '@/shared/kernel/instant';

export type DomainEvent = {
  readonly eventId: DomainEventId;
  readonly occurredAt: Instant;
  readonly type: string;
};

export type DomainEventHandler<Event extends DomainEvent = DomainEvent> = (event: Event) => void;

export type Unsubscribe = () => void;

export interface DomainEventPublisher {
  publish(event: DomainEvent): void;
  subscribe(handler: DomainEventHandler): Unsubscribe;
}

export class InMemoryDomainEventPublisher implements DomainEventPublisher {
  private readonly handlers = new Set<DomainEventHandler>();

  publish(event: DomainEvent): void {
    for (const handler of this.handlers) {
      handler(event);
    }
  }

  subscribe(handler: DomainEventHandler): Unsubscribe {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }
}