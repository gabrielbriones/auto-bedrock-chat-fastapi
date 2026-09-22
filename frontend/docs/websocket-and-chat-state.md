# WebSocket And Chat State

## Who calls whom?

The short answer is: `ChatSessionStore` does not directly call `SocketClient`.

It receives two ports:

- `MessagingGateway`: send chat messages and receive protocol-free messaging events.
- `ConnectionSource`: observe connection state.

`createContainer` injects adapters backed by the same `SocketClient`. This lets the store coordinate chat behavior without owning connection lifecycle.

```mermaid
flowchart TB
    subgraph Transport[Shared transport]
        Socket[SocketClient]
        Bus[MessageBus]
    end

    subgraph Adapters[Infrastructure adapters]
        MessagingGateway[WsMessagingGateway]
        AuthGateway[WsAuthGateway]
    end

    subgraph Application[Application layer]
        ChatStore[ChatSessionStore]
        IdentityStore[IdentityStore]
    end

    Socket -- onFrame --> Bus
    Socket -- implements --> ConnectionSource[ConnectionSource port]
    Socket -- send --> MessagingGateway
    Bus -- subscribe --> MessagingGateway
    Bus -- subscribe --> AuthGateway
    MessagingGateway -- implements --> MessagingPort[MessagingGateway port]
    AuthGateway --> IdentityStore
    ConnectionSource --> ChatStore
    MessagingPort --> ChatStore
```

`ChatSocketProvider` is the only component that calls `connect()` and `dispose()`. Route changes do not recreate the socket because the provider is mounted above the router.

## Incoming frame flow

A backend frame moves from the browser transport toward the UI as follows:

```mermaid
sequenceDiagram
    participant Backend
    participant Socket as SocketClient
    participant Bus as MessageBus
    participant Gateway as WsMessagingGateway
    participant Store as ChatSessionStore
    participant Hook as useContainerStore('chatSession')
    participant UI as ChatPanel

    Backend-->>Socket: raw WebSocket frame
    Socket->>Bus: onFrame(frame)
    Bus->>Bus: parse and validate ServerFrame
    Bus->>Gateway: parsed ServerFrame
    Gateway->>Gateway: map protocol frame to MessagingEvent
    Gateway->>Store: onEvent(event)
    Store->>Store: mutate domain turns/session/transients
    Store-->>Hook: notify subscribers
    Hook->>Store: getSnapshot()
    Store-->>Hook: ChatSessionSnapshot
    Hook-->>UI: render transcript and status
```

Examples of mapping:

| Backend frame | Domain event | Store behavior |
| --- | --- | --- |
| `connection_established` | `session-established` | Stores the session id |
| `ai_response` | `answered` | Resolves the pending optimistic turn |
| `ai_response` with `error` | `failed` | Fails the pending turn |
| `error` | `failed` | Fails the pending turn or shows a transient |
| `typing` | `typing` | Currently observes/logs the first frame only; streaming is a later phase |

Invalid frames are rejected by `MessageBus` and do not reach the store.

## Outgoing message flow

```mermaid
sequenceDiagram
    participant User
    participant UI as ChatPanel / composer
    participant Store as ChatSessionStore
    participant Gateway as WsMessagingGateway
    participant Socket as SocketClient
    participant Backend

    User->>UI: submit text
    UI->>Store: send(text)
    Store->>Store: trim and refuse invalid/concurrent sends
    Store->>Store: create optimistic user turn
    Store->>Gateway: sendChat(text)
    Gateway->>Gateway: create { type: chat, message }
    Gateway->>Socket: send(JSON frame)
    Socket->>Backend: WebSocket send
    Store-->>UI: snapshot includes optimistic message
    Backend-->>Socket: ai_response
    Socket->>Gateway: parsed response via MessageBus
    Gateway->>Store: answered event
    Store->>Store: reconcile response into pending turn
    Store-->>UI: snapshot includes assistant response
```

If the socket is closed, the send is not queued. The optimistic turn is marked abandoned and the caller receives `dropped-closed`.

## Connection state flow

```mermaid
stateDiagram-v2
    [*] --> idle
    idle --> connecting: ChatSocketProvider.connect()
    connecting --> open: WebSocket opens
    connecting --> reconnecting: open timeout or failure
    open --> reconnecting: unexpected close
    reconnecting --> open: retry succeeds
    reconnecting --> closed: intentional close / dispose
    open --> closed: intentional close / dispose

    state open {
        [*] --> canSend
        canSend --> waitingForResponse: chat sent
        waitingForResponse --> canSend: ai_response or error
    }
```

`ChatSessionStore` reacts to connection state changes by:

- updating its connection view;
- clearing the session id when the connection is no longer open;
- abandoning an unresolved turn so the UI cannot remain waiting forever;
- emitting a new snapshot for React.

## State ownership summary

| Concern | Owner | React reads it through |
| --- | --- | --- |
| WebSocket open/retry/close | `SocketClient` | `ChatSessionStore` connection port |
| Frame validation and routing | `MessageBus` | Not directly |
| Protocol-to-domain translation | `WsMessagingGateway` | Not directly |
| Chat session, turns, transcript | `ChatSessionStore` | `useContainerStore('chatSession')` |
| Socket lifetime | `ChatSocketProvider` | Not applicable |
| Visual rendering | `ChatPanel`, `Transcript`, `ConnectionBadge` | Snapshot values |

This separation is the important mental model: transport emits facts, adapters translate them, the store decides application state, and React renders the resulting snapshot.
