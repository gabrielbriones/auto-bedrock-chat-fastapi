"""Interactive WebSocket Chat Client

This example demonstrates how to interact with the auto-bedrock-chat-fastapi
WebSocket endpoint for real-time chat communication with an interactive prompt.

Features:
- Interactive chat with real-time message display
- Configure WebSocket endpoint
- Handle authentication (Bearer token, API Key, Basic Auth, OAuth2)
- Send and receive chat messages
- Handle typing indicators
- Manage session lifecycle
- Error handling and reconnection logic

Usage:
    python interactive.py --url ws://localhost:8000/ws/chat --auth bearer --token YOUR_TOKEN
"""

import asyncio
import json
import logging
from argparse import ArgumentParser
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, Optional

import websockets
from websockets.asyncio.client import ClientConnection, connect

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class AuthType(str, Enum):
    """Supported authentication types"""

    BEARER_TOKEN = "bearer_token"
    API_KEY = "api_key"
    BASIC_AUTH = "basic_auth"
    OAUTH2 = "oauth2"
    NONE = "none"


class WebSocketConfig:
    """Configuration for WebSocket client connection"""

    def __init__(
        self,
        endpoint: str,
        auth_type: AuthType = AuthType.NONE,
        **auth_kwargs,
    ):
        """Initialize WebSocket configuration

        Args:
            endpoint: WebSocket endpoint URL (e.g., ws://localhost:8000/ws/chat)
            auth_type: Type of authentication to use
            **auth_kwargs: Authentication-specific parameters
                - For BEARER_TOKEN: token
                - For API_KEY: api_key, api_key_header (default: X-API-Key)
                - For BASIC_AUTH: username, password
                - For OAUTH2: client_id, client_secret, token_url, scope
        """
        self.endpoint = endpoint
        self.auth_type = auth_type
        self.auth_kwargs = auth_kwargs
        self.session_id: Optional[str] = None
        self.is_authenticated = False
        self.is_connected = False

    def get_auth_payload(self) -> Dict[str, Any]:
        """Get authentication payload for WebSocket message

        Returns:
            Dictionary with authentication parameters
        """
        if self.auth_type == AuthType.NONE:
            return {}

        payload = {"type": "auth", "auth_type": self.auth_type.value}

        if self.auth_type == AuthType.BEARER_TOKEN:
            payload["token"] = self.auth_kwargs.get("token")

        elif self.auth_type == AuthType.API_KEY:
            payload["api_key"] = self.auth_kwargs.get("api_key")
            payload["api_key_header"] = self.auth_kwargs.get("api_key_header", "X-API-Key")

        elif self.auth_type == AuthType.BASIC_AUTH:
            payload["username"] = self.auth_kwargs.get("username")
            payload["password"] = self.auth_kwargs.get("password")

        elif self.auth_type == AuthType.OAUTH2:
            payload["client_id"] = self.auth_kwargs.get("client_id")
            payload["client_secret"] = self.auth_kwargs.get("client_secret")
            payload["token_url"] = self.auth_kwargs.get("token_url")
            if "scope" in self.auth_kwargs:
                payload["scope"] = self.auth_kwargs.get("scope")

        return payload


class WebSocketChatClient:
    """WebSocket client for interacting with auto-bedrock-chat-fastapi"""

    def __init__(
        self,
        config: WebSocketConfig,
        on_message: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_connected: Optional[Callable[[str], None]] = None,
        redraw_prompt: Optional[Callable[[], None]] = None,
    ):
        """Initialize WebSocket chat client

        Args:
            config: WebSocket configuration
            on_message: Callback for incoming messages
            on_error: Callback for errors
            on_connected: Callback for successful connection
            redraw_prompt: Callback to redraw the input prompt
        """
        self.config = config
        self.on_message = on_message or self._default_on_message
        self.on_error = on_error or self._default_on_error
        self.on_connected = on_connected or self._default_on_connected
        self.redraw_prompt = redraw_prompt
        self.ws: Optional[ClientConnection] = None
        self._receive_task: Optional[asyncio.Task] = None

    @staticmethod
    def _default_on_message(message: Dict[str, Any]) -> None:
        """Default message handler"""
        msg_type = message.get("type", "unknown")
        if msg_type == "ai_response":
            print(f"\n🤖 Assistant: {message.get('message', '')}")
            if message.get("tool_calls"):
                print(f"   Tool calls: {len(message['tool_calls'])}")
        elif msg_type == "typing":
            print(f"✍️  {message.get('message', '')}", flush=True)
        elif msg_type == "connection_established":
            print(f"✅ {message.get('message', '')}")
        else:
            # Filter out auth_configured messages from generic output
            if msg_type != "auth_configured":
                print(f"📨 {msg_type}: {message}")

    @staticmethod
    def _default_on_error(error: str) -> None:
        """Default error handler"""
        print(f"❌ Error: {error}")

    @staticmethod
    def _default_on_connected(session_id: str) -> None:
        """Default connection handler"""
        print(f"✨ Connected with session ID: {session_id}")

    async def connect(self) -> bool:
        """Connect to WebSocket endpoint

        Returns:
            True if connection successful, False otherwise
        """
        try:
            logger.info(f"Connecting to {self.config.endpoint}...")
            # Increase max_size to handle large responses (default is 1MB, we allow up to 100MB)
            self.ws = await connect(self.config.endpoint, max_size=100 * 1024 * 1024)
            self.config.is_connected = True
            logger.info("WebSocket connected")

            # Start receiving messages
            self._receive_task = asyncio.create_task(self._receive_messages())

            # Authenticate if needed
            if self.config.auth_type != AuthType.NONE:
                auth_payload = self.config.get_auth_payload()
                await self.send_message(auth_payload)
                logger.info(f"Authentication message sent ({self.config.auth_type.value})")

            return True

        except Exception as e:
            error_msg = f"Failed to connect: {str(e)}"
            logger.error(error_msg)
            self.on_error(error_msg)
            self.config.is_connected = False
            return False

    async def disconnect(self) -> None:
        """Disconnect from WebSocket"""
        try:
            if self.ws:
                await self.ws.close()
            if self._receive_task:
                self._receive_task.cancel()
            logger.info("WebSocket disconnected")
            self.config.is_connected = False
        except Exception as e:
            logger.error(f"Error disconnecting: {str(e)}")

    async def send_message(self, data: Dict[str, Any]) -> bool:
        """Send message to WebSocket

        Args:
            data: Message data to send

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.ws or not self.config.is_connected:
            self.on_error("Not connected to WebSocket")
            return False

        try:
            message_json = json.dumps(data)
            await self.ws.send(message_json)
            logger.debug(f"Message sent: {message_json}")
            return True
        except Exception as e:
            error_msg = f"Failed to send message: {str(e)}"
            logger.error(error_msg)
            self.on_error(error_msg)
            return False

    async def send_chat_message(self, message: str) -> bool:
        """Send chat message

        Args:
            message: Chat message text

        Returns:
            True if sent successfully, False otherwise
        """
        return await self.send_message(
            {
                "type": "chat",
                "message": message,
            }
        )

    async def get_history(self) -> bool:
        """Request conversation history

        Returns:
            True if request sent successfully, False otherwise
        """
        return await self.send_message(
            {
                "type": "history",
            }
        )

    async def clear_history(self) -> bool:
        """Clear conversation history

        Returns:
            True if request sent successfully, False otherwise
        """
        return await self.send_message(
            {
                "type": "clear",
            }
        )

    async def ping(self) -> bool:
        """Send ping message to test connection

        Returns:
            True if sent successfully, False otherwise
        """
        return await self.send_message(
            {
                "type": "ping",
            }
        )

    async def logout(self) -> bool:
        """Send logout message

        Returns:
            True if sent successfully, False otherwise
        """
        return await self.send_message(
            {
                "type": "logout",
            }
        )

    async def _receive_messages(self) -> None:
        """Receive and process incoming messages"""
        try:
            async for message_text in self.ws:
                try:
                    message = json.loads(message_text)
                    logger.debug(f"Message received: {message}")

                    # Extract session ID from first connection message
                    if message.get("type") == "connection_established":
                        self.config.session_id = message.get("session_id")
                        self.config.is_authenticated = True
                        self.on_connected(self.config.session_id)

                    # Handle authentication response
                    elif message.get("type") == "auth_response":
                        if message.get("success"):
                            self.config.is_authenticated = True
                            logger.info("Authentication successful")
                        else:
                            self.config.is_authenticated = False
                            error_msg = message.get("message", "Authentication failed")
                            logger.error(error_msg)
                            self.on_error(error_msg)

                    # Call message handler
                    self.on_message(message)

                    # Redraw prompt after AI response
                    if message.get("type") == "ai_response" and self.redraw_prompt:
                        self.redraw_prompt()

                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse message: {str(e)}")
                    self.on_error(f"Invalid message format: {str(e)}")

        except asyncio.CancelledError:
            logger.debug("Message receive task cancelled")
        except Exception as e:
            logger.error(f"Error receiving messages: {str(e)}")
            self.on_error(f"Connection error: {str(e)}")
            self.config.is_connected = False


async def interactive_chat(client: WebSocketChatClient) -> None:
    """Interactive chat loop

    Args:
        client: WebSocketChatClient instance
    """
    print("\n" + "=" * 60)
    print("WebSocket Chat Client - Interactive Mode")
    print("=" * 60)
    print("Commands:")
    print("  - Type a message to chat")
    print("  - /history - Get conversation history")
    print("  - /clear - Clear conversation history")
    print("  - /ping - Test connection")
    print("  - /logout - Logout")
    print("  - /quit - Exit")
    print("=" * 60 + "\n")

    loop = asyncio.get_event_loop()
    show_next_prompt = [True]  # Use list to allow modification in nested function

    def show_prompt():
        """Display the input prompt"""
        if show_next_prompt[0]:
            import sys
            sys.stdout.write("You: ")
            sys.stdout.flush()

    def redraw_prompt_callback():
        """Callback to redraw prompt after receiving a response"""
        show_next_prompt[0] = True
        show_prompt()

    # Set the redraw prompt callback on the client
    client.redraw_prompt = redraw_prompt_callback

    # Show initial prompt
    show_prompt()

    # Create a task to handle input
    async def get_user_input():
        """Get user input in a separate task"""
        return await loop.run_in_executor(None, input, "")

    input_task = None

    while client.config.is_connected:
        try:
            # Start input task if not already running
            if input_task is None:
                input_task = asyncio.create_task(get_user_input())

            # Wait for either input or a brief timeout to allow messages to be received
            done, pending = await asyncio.wait(
                [input_task],
                timeout=0.1,
            )

            if done:
                # User input received
                user_input = input_task.result()
                input_task = None
                show_next_prompt[0] = False  # Don't show prompt yet

                if user_input.lower() == "/quit":
                    print("Goodbye!")
                    break

                elif user_input.lower() == "/history":
                    await client.get_history()
                    print()  # Add newline after command
                    show_next_prompt[0] = True
                    show_prompt()

                elif user_input.lower() == "/clear":
                    if await client.clear_history():
                        print("✅ History cleared")
                    print()  # Add newline after command
                    show_next_prompt[0] = True
                    show_prompt()

                elif user_input.lower() == "/ping":
                    if await client.ping():
                        print("📡 Ping sent")
                    print()  # Add newline after command
                    show_next_prompt[0] = True
                    show_prompt()

                elif user_input.lower() == "/logout":
                    if await client.logout():
                        print("👋 Logged out")
                    print()  # Add newline after command
                    show_next_prompt[0] = True
                    show_prompt()

                elif user_input.strip():
                    await client.send_chat_message(user_input)
                    # Don't show prompt - wait for response to be received

        except EOFError:
            break
        except KeyboardInterrupt:
            print("\n\nInterrupted by user")
            if input_task:
                input_task.cancel()
            break
        except Exception as e:
            logger.error(f"Error in chat loop: {str(e)}")
            if input_task:
                input_task.cancel()
            break

    # Clean up
    if input_task and not input_task.done():
        input_task.cancel()


async def demo_conversation(client: WebSocketChatClient) -> None:
    """Demo conversation mode (non-interactive)

    Args:
        client: WebSocketChatClient instance
    """
    print("\n" + "=" * 60)
    print("WebSocket Chat Client - Demo Mode")
    print("=" * 60 + "\n")

    demo_messages = [
        "Hello! What's the weather like?",
        "Tell me a joke",
        "What is 2+2?",
    ]

    for idx, message in enumerate(demo_messages, 1):
        print(f"\n[{idx}/{len(demo_messages)}] Sending: {message}")
        await client.send_chat_message(message)
        await asyncio.sleep(2)  # Wait for response

    await asyncio.sleep(1)
    print("\n✅ Demo completed")


async def main():
    """Main entry point"""
    parser = ArgumentParser(description="Interactive WebSocket Chat Client for auto-bedrock-chat-fastapi")

    # Connection settings
    parser.add_argument(
        "--url",
        default="ws://localhost:8000/bedrock-chat/ws",
        help="WebSocket endpoint URL (default: ws://localhost:8000/bedrock-chat/ws)",
    )

    # Authentication settings
    parser.add_argument(
        "--auth",
        choices=["none", "bearer", "api_key", "basic", "oauth2"],
        default="none",
        help="Authentication type (default: none)",
    )

    parser.add_argument(
        "--token",
        help="Bearer token for authentication",
    )

    parser.add_argument(
        "--api-key",
        help="API key for authentication",
    )

    parser.add_argument(
        "--api-key-header",
        default="X-API-Key",
        help="API key header name (default: X-API-Key)",
    )

    parser.add_argument(
        "--username",
        help="Username for basic authentication",
    )

    parser.add_argument(
        "--password",
        help="Password for basic authentication",
    )

    parser.add_argument(
        "--client-id",
        help="OAuth2 client ID",
    )

    parser.add_argument(
        "--client-secret",
        help="OAuth2 client secret",
    )

    parser.add_argument(
        "--token-url",
        help="OAuth2 token URL",
    )

    parser.add_argument(
        "--scope",
        help="OAuth2 scope",
    )

    # Mode settings
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run demo mode with predefined messages (non-interactive)",
    )

    args = parser.parse_args()

    # Build authentication kwargs
    auth_kwargs = {}

    if args.auth == "bearer":
        if not args.token:
            parser.error("--token is required for bearer authentication")
        auth_kwargs["token"] = args.token

    elif args.auth == "api_key":
        if not args.api_key:
            parser.error("--api-key is required for api_key authentication")
        auth_kwargs["api_key"] = args.api_key
        auth_kwargs["api_key_header"] = args.api_key_header

    elif args.auth == "basic":
        if not args.username or not args.password:
            parser.error("--username and --password are required for basic authentication")
        auth_kwargs["username"] = args.username
        auth_kwargs["password"] = args.password

    elif args.auth == "oauth2":
        if not args.client_id or not args.client_secret or not args.token_url:
            parser.error("--client-id, --client-secret, and --token-url are required for oauth2")
        auth_kwargs["client_id"] = args.client_id
        auth_kwargs["client_secret"] = args.client_secret
        auth_kwargs["token_url"] = args.token_url
        if args.scope:
            auth_kwargs["scope"] = args.scope

    # Create configuration
    auth_type = AuthType(args.auth) if args.auth != "none" else AuthType.NONE

    config = WebSocketConfig(
        endpoint=args.url,
        auth_type=auth_type,
        **auth_kwargs,
    )

    # Create client (redraw_prompt will be set in interactive_chat if needed)
    client = WebSocketChatClient(config, redraw_prompt=None)

    try:
        # Connect
        if not await client.connect():
            return 1

        # Wait a bit for connection to be established
        await asyncio.sleep(0.5)

        # Run demo or interactive mode
        if args.demo:
            await demo_conversation(client)
        else:
            await interactive_chat(client)

        return 0

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0

    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        return 1

    finally:
        await client.disconnect()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
