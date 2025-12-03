# WebSocket Chat Client Examples

This directory contains examples for interacting with the auto-bedrock-chat-fastapi WebSocket endpoint.

## Overview

The `interactive.py` script provides an interactive real-time chat interface with the Bedrock AI assistant. It handles:

- Interactive chat with live message display
- Connection management
- Authentication (Bearer token, API Key, Basic Auth, OAuth2)
- Message sending and receiving
- Session management
- Error handling and reconnection

## Installation

```bash
pip install websockets
```

## Quick Start

### Interactive Chat (No Authentication)

```bash
python interactive.py
```

This connects to the default WebSocket endpoint (`ws://localhost:8000/ws/chat`) and starts an interactive chat.

### Interactive Chat (Bearer Token)

```bash
python interactive.py --auth bearer --token YOUR_TOKEN
```

### Demo Mode (Predefined Messages)

```bash
python interactive.py --demo --auth bearer --token YOUR_TOKEN
```

## Usage Examples

### Bearer Token Authentication

```bash
python interactive.py --url ws://localhost:8000/ws/chat --auth bearer --token "sk-1234567890"
```

### API Key Authentication

```bash
python interactive.py --auth api_key --api-key "your-api-key" --api-key-header "Authorization"
```

### Basic Authentication

```bash
python interactive.py --auth basic --username user@example.com --password "password123"
```

### OAuth2 Authentication

```bash
python interactive.py \
  --auth oauth2 \
  --client-id "your-client-id" \
  --client-secret "your-client-secret" \
  --token-url "https://oauth.example.com/token" \
  --scope "read write"
```

### Custom WebSocket Endpoint

```bash
python interactive.py --url ws://api.example.com:8000/ws/chat --auth bearer --token YOUR_TOKEN
```

## Interactive Commands

Once connected, you can use the following commands:

- **Type a message** - Send a chat message
- **/history** - Get conversation history
- **/clear** - Clear conversation history
- **/ping** - Test connection
- **/logout** - Logout
- **/quit** - Exit

Example:

```
You: /history
You: Tell me about Python
You: /clear
You: /quit
```

## Using the Client Programmatically

### Basic Usage

```python
import asyncio
from app import WebSocketConfig, WebSocketChatClient, AuthType

async def main():
    # Create configuration
    config = WebSocketConfig(
        endpoint="ws://localhost:8000/ws/chat",
        auth_type=AuthType.BEARER_TOKEN,
        token="your-token"
    )

    # Create client
    client = WebSocketChatClient(config)

    # Connect
    if await client.connect():
        # Send a message
        await client.send_chat_message("Hello!")

        # Wait for response
        await asyncio.sleep(2)

        # Disconnect
        await client.disconnect()

asyncio.run(main())
```

### Custom Message Handlers

```python
def handle_message(message):
    msg_type = message.get("type")
    if msg_type == "ai_response":
        print(f"Assistant: {message['message']}")
    elif msg_type == "typing":
        print("Assistant is typing...")

def handle_error(error):
    print(f"Error: {error}")

def handle_connected(session_id):
    print(f"Connected: {session_id}")

config = WebSocketConfig(
    endpoint="ws://localhost:8000/ws/chat",
    auth_type=AuthType.BEARER_TOKEN,
    token="your-token"
)

client = WebSocketChatClient(
    config,
    on_message=handle_message,
    on_error=handle_error,
    on_connected=handle_connected
)
```

### Conversation Flow

```python
async def conversation():
    config = WebSocketConfig(
        endpoint="ws://localhost:8000/ws/chat",
        auth_type=AuthType.API_KEY,
        api_key="your-api-key"
    )

    client = WebSocketChatClient(config)

    try:
        await client.connect()

        # Send multiple messages
        messages = [
            "What is machine learning?",
            "Can you give me an example?",
            "How does it differ from deep learning?"
        ]

        for msg in messages:
            await client.send_chat_message(msg)
            await asyncio.sleep(3)  # Wait for response

        # Get history
        await client.get_history()
        await asyncio.sleep(1)

        # Clear history
        await client.clear_history()

    finally:
        await client.disconnect()

asyncio.run(conversation())
```

## Message Format

### Chat Message

```json
{
  "type": "chat",
  "message": "Your message here"
}
```

### Authentication Message

Bearer Token:

```json
{
  "type": "auth",
  "auth_type": "bearer_token",
  "token": "your-token"
}
```

API Key:

```json
{
  "type": "auth",
  "auth_type": "api_key",
  "api_key": "your-key",
  "api_key_header": "X-API-Key"
}
```

Basic Auth:

```json
{
  "type": "auth",
  "auth_type": "basic_auth",
  "username": "user",
  "password": "pass"
}
```

OAuth2:

```json
{
  "type": "auth",
  "auth_type": "oauth2",
  "client_id": "id",
  "client_secret": "secret",
  "token_url": "https://...",
  "scope": "read write"
}
```

### Server Responses

Connection Established:

```json
{
  "type": "connection_established",
  "session_id": "session-123",
  "message": "Connected to AI assistant",
  "timestamp": "2024-12-03T10:00:00"
}
```

AI Response:

```json
{
  "type": "ai_response",
  "message": "Response text",
  "tool_calls": [],
  "tool_results": [],
  "timestamp": "2024-12-03T10:00:05"
}
```

Typing Indicator:

```json
{
  "type": "typing",
  "message": "AI is thinking...",
  "timestamp": "2024-12-03T10:00:01"
}
```

Authentication Response:

```json
{
  "type": "auth_response",
  "success": true,
  "message": "Authentication successful"
}
```

Error:

```json
{
  "type": "error",
  "message": "Error description"
}
```

## Configuration Environment Variables

You can set default values using environment variables:

```bash
# WebSocket endpoint
export BEDROCK_WS_ENDPOINT="ws://localhost:8000/ws/chat"

# Authentication
export BEDROCK_AUTH_TYPE="bearer_token"
export BEDROCK_AUTH_TOKEN="your-token"
export BEDROCK_API_KEY="your-api-key"
```

## Debugging

Enable debug logging:

```bash
python interactive.py --auth bearer --token YOUR_TOKEN
```

Check logs to see WebSocket communication:

```
2024-12-03 10:00:00,123 - __main__ - INFO - Connecting to ws://localhost:8000/ws/chat...
2024-12-03 10:00:00,456 - __main__ - INFO - WebSocket connected
2024-12-03 10:00:00,789 - __main__ - DEBUG - Message sent: {"type": "chat", "message": "..."}
```

## Connection Options

| Option   | Default                     | Description                                                |
| -------- | --------------------------- | ---------------------------------------------------------- |
| `--url`  | ws://localhost:8000/ws/chat | WebSocket endpoint URL                                     |
| `--auth` | none                        | Authentication type (none, bearer, api_key, basic, oauth2) |
| `--demo` | false                       | Run demo mode (non-interactive)                            |

## Error Handling

The client automatically handles:

- Connection failures
- Message parsing errors
- Authentication failures
- Network disconnections

Errors are passed to the `on_error` callback or printed to console in interactive mode.

## Best Practices

1. **Always authenticate** - Use appropriate authentication for production
2. **Handle errors gracefully** - Implement error callbacks
3. **Disconnect properly** - Use try/finally to ensure cleanup
4. **Test connection** - Use `/ping` to verify connectivity
5. **Monitor session** - Check session_id for debugging

## Troubleshooting

### Connection Refused

```
Error: Failed to connect: [Errno 111] Connection refused
```

Make sure the server is running:

```bash
python -m uvicorn app:app --reload
```

### Authentication Failed

```
Error: Authentication failed
```

Verify your credentials:

```bash
python interactive.py --auth bearer --token "correct-token"
```

### Message Format Error

```
Error: Invalid message format
```

Check that messages are valid JSON and include required fields.

### Message Too Big Error

```
Error: sent 1009 (message too big) frame exceeds limit of 1048576 bytes
```

This occurs when the server sends a response larger than the WebSocket frame size limit. The client automatically handles this by allowing up to 100MB frames. If you're still getting this error:

- The server may be sending extremely large responses (> 100MB)
- Consider filtering results (e.g., limit job list to recent jobs)
- Split large requests into smaller ones
- Increase the max_size in the client code if needed:

```python
self.ws = await connect(endpoint, max_size=200 * 1024 * 1024)  # 200MB
```

## See Also

- [Main README](../../README.md)
- [WebSocket Handler Documentation](../../docs/AUTHENTICATION.md)
- [Configuration Guide](../../docs/CONFIGURATION.md)
