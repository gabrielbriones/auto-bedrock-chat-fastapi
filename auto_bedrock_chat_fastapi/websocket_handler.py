"""WebSocket handler for real-time chat communication"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import WebSocket, WebSocketDisconnect

from .auth_handler import AuthenticationHandler, AuthType, Credentials
from .bedrock_client import BedrockClient
from .config import ChatConfig
from .exceptions import WebSocketError
from .session_manager import ChatMessage, ChatSessionManager
from .tools_generator import ToolsGenerator

logger = logging.getLogger(__name__)


class WebSocketChatHandler:
    """Handles WebSocket connections and chat communication"""

    def __init__(
        self,
        session_manager: ChatSessionManager,
        bedrock_client: BedrockClient,
        tools_generator: ToolsGenerator,
        config: ChatConfig,
        app_base_url: str = "http://localhost:8000",
    ):
        self.session_manager = session_manager
        self.bedrock_client = bedrock_client
        self.tools_generator = tools_generator
        self.config = config
        self.app_base_url = app_base_url.rstrip("/")

        # HTTP client for making internal API calls
        self.http_client = httpx.AsyncClient(timeout=config.timeout)

        # Statistics
        self._total_messages_handled = 0
        self._total_tool_calls_executed = 0
        self._total_errors = 0

    async def handle_connection(self, websocket: WebSocket, user_id: Optional[str] = None):
        """Handle new WebSocket connection"""

        try:
            # Accept WebSocket connection
            await websocket.accept()

            # Extract connection info
            user_agent = websocket.headers.get("user-agent")
            ip_address = self._get_client_ip(websocket)

            # Create chat session
            session_id = await self.session_manager.create_session(
                websocket=websocket,
                user_id=user_id,
                user_agent=user_agent,
                ip_address=ip_address,
            )

            logger.info(f"WebSocket connected: session={session_id}, user={user_id}, ip={ip_address}")

            # Send welcome message
            await self._send_message(
                websocket,
                {
                    "type": "connection_established",
                    "session_id": session_id,
                    "message": "Connected to AI assistant",
                    "timestamp": datetime.now().isoformat(),
                },
            )

            # Main message handling loop
            await self._message_loop(websocket)

        except WebSocketDisconnect:
            logger.info("WebSocket disconnected normally")
        except Exception as e:
            logger.error(f"WebSocket connection error: {str(e)}")
            self._total_errors += 1

            try:
                await self._send_error(websocket, f"Connection error: {str(e)}")
            except BaseException:
                pass  # Connection already closed
        finally:
            # Clean up session
            await self.session_manager.remove_session(websocket)

    async def _message_loop(self, websocket: WebSocket):
        """Main message handling loop"""

        while True:
            try:
                # Receive message from client
                data = await websocket.receive_text()

                # Parse JSON
                try:
                    message_data = json.loads(data)
                except json.JSONDecodeError as e:
                    await self._send_error(websocket, f"Invalid JSON: {str(e)}")
                    continue

                # Handle different message types
                message_type = message_data.get("type", "chat")

                if message_type == "chat":
                    await self._handle_chat_message(websocket, message_data)
                elif message_type == "ping":
                    await self._handle_ping(websocket, message_data)
                elif message_type == "history":
                    await self._handle_history_request(websocket, message_data)
                elif message_type == "clear":
                    await self._handle_clear_history(websocket, message_data)
                elif message_type == "auth":
                    await self._handle_auth_message(websocket, message_data)
                elif message_type == "logout":
                    await self._handle_logout(websocket, message_data)
                else:
                    await self._send_error(websocket, f"Unknown message type: {message_type}")

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"Error in message loop: {str(e)}")
                self._total_errors += 1
                await self._send_error(websocket, f"Message processing error: {str(e)}")

    async def _handle_chat_message(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle incoming chat message"""

        session = await self.session_manager.get_session(websocket)
        if not session:
            await self._send_error(websocket, "Session not found")
            return

        user_message = data.get("message", "")
        if not user_message.strip():
            await self._send_error(websocket, "Empty message")
            return

        self._total_messages_handled += 1

        try:
            # Check if authentication is required before sending messages to LLM
            if self.config.require_tool_auth:
                auth_type_str = session.credentials.get_auth_type_string()
                if auth_type_str == "none":
                    await self._send_error(
                        websocket, "Authentication is required before sending messages. Please authenticate first."
                    )
                    return

            # Add user message to history
            user_chat_message = ChatMessage(role="user", content=user_message, metadata={"source": "websocket"})
            logger.debug(f"Received user message: {user_message}")
            await self.session_manager.add_message(session.session_id, user_chat_message)

            # Send typing indicator
            await self._send_message(
                websocket,
                {
                    "type": "typing",
                    "message": "AI is thinking...",
                    "timestamp": datetime.now().isoformat(),
                },
            )

            # Get conversation context
            context_messages = await self.session_manager.get_context_messages(session.session_id)

            # Convert to Bedrock format
            bedrock_messages = self._format_messages_for_bedrock(context_messages)

            # Get tools description
            tools_desc = self.tools_generator.generate_tools_desc()

            # Call Bedrock
            response = await self.bedrock_client.chat_completion(
                messages=bedrock_messages,
                tools_desc=tools_desc,
                **self.config.get_bedrock_params(),
            )
            logger.debug(f"Bedrock response: {response.get('content', '')}")

            # Process tool calls recursively if any
            (
                final_response,
                all_tool_results,
            ) = await self._handle_tool_calls_recursively(session.session_id, response, tools_desc, websocket, session)

            # Add the final AI response to history (if not already added)
            if not final_response.get("tool_calls"):
                ai_message = ChatMessage(
                    role="assistant",
                    content=final_response.get("content", ""),
                    tool_calls=[],
                    tool_results=all_tool_results,
                    metadata=final_response.get("metadata", {}),
                )
                await self.session_manager.add_message(session.session_id, ai_message)

            # Send response to client (use final response data)
            await self._send_message(
                websocket,
                {
                    "type": "ai_response",
                    "message": final_response.get("content", ""),
                    "tool_calls": final_response.get("tool_calls", []),
                    "tool_results": all_tool_results,
                    "timestamp": datetime.now().isoformat(),
                    "metadata": final_response.get("metadata", {}),
                },
            )

        except Exception as e:
            logger.error(f"Error processing chat message: {str(e)}")
            self._total_errors += 1

            # Send error to user
            error_response = self._create_error_response(str(e))
            await self._send_message(
                websocket,
                {
                    "type": "ai_response",
                    "message": error_response,
                    "error": True,
                    "timestamp": datetime.now().isoformat(),
                },
            )

    async def _execute_tool_calls(self, tool_calls: List[Dict[str, Any]], session=None) -> List[Dict[str, Any]]:
        """Execute tool calls by making HTTP requests to API endpoints

        Args:
            tool_calls: List of tool calls to execute
            session: ChatSession with authentication info (optional)
        """

        results = []

        for tool_call in tool_calls[: self.config.max_tool_calls]:
            try:
                logger.debug(f"Executing tool call: {tool_call}")
                self._total_tool_calls_executed += 1

                function_name = tool_call.get("name")
                arguments = tool_call.get("arguments", {})

                # Get tool metadata
                tool_metadata = self.tools_generator.get_tool_metadata(function_name)
                if not tool_metadata:
                    logger.warning(f"Unknown tool requested: {function_name}")
                    results.append(
                        {
                            "tool_call_id": tool_call.get("id"),
                            "name": function_name,
                            "error": f"Unknown tool: {function_name}",
                        }
                    )
                    continue

                # Validate arguments
                if not self.tools_generator.validate_tool_call(function_name, arguments):
                    logger.warning(f"Invalid arguments for tool {function_name}: {arguments}")
                    results.append(
                        {
                            "tool_call_id": tool_call.get("id"),
                            "name": function_name,
                            "error": "Invalid arguments",
                        }
                    )
                    continue

                # Execute tool call with authentication if available
                result = await self._execute_single_tool_call(tool_metadata, arguments, session)
                logger.debug(f"Tool call result for {function_name}: {result}")

                results.append(
                    {
                        "tool_call_id": tool_call.get("id"),
                        "name": function_name,
                        "result": result,
                    }
                )

            except Exception as e:
                logger.error(f"Error executing tool call {function_name}: {str(e)}")
                results.append(
                    {
                        "tool_call_id": tool_call.get("id"),
                        "name": function_name,
                        "error": str(e),
                    }
                )

        return results

    async def _execute_tool_calls_with_progress(
        self, tool_calls: List[Dict[str, Any]], websocket: WebSocket, round_number: int
    ) -> List[Dict[str, Any]]:
        """Execute tool calls with progress updates to the UI"""

        results = []
        total_tools = len(tool_calls[: self.config.max_tool_calls])

        for i, tool_call in enumerate(tool_calls[: self.config.max_tool_calls], 1):
            try:
                logger.debug(f"Executing tool call {i}/{total_tools}: {tool_call}")
                self._total_tool_calls_executed += 1

                function_name = tool_call.get("name")
                arguments = tool_call.get("arguments", {})

                # Send progress update
                await self._send_message(
                    websocket,
                    {
                        "type": "typing",
                        "message": f"Calling {function_name}... ({i}/{total_tools})",
                        "timestamp": datetime.now().isoformat(),
                    },
                )

                # Get tool metadata
                tool_metadata = self.tools_generator.get_tool_metadata(function_name)
                if not tool_metadata:
                    logger.warning(f"Unknown tool requested: {function_name}")
                    results.append(
                        {
                            "tool_call_id": tool_call.get("id"),
                            "name": function_name,
                            "error": f"Unknown tool: {function_name}",
                        }
                    )
                    continue

                # Validate arguments
                if not self.tools_generator.validate_tool_call(function_name, arguments):
                    results.append(
                        {
                            "tool_call_id": tool_call.get("id"),
                            "name": function_name,
                            "error": "Invalid arguments",
                        }
                    )
                    continue

                # Execute tool call
                result = await self._execute_single_tool_call(tool_metadata, arguments)
                logger.debug(f"Tool call result for {function_name}: {result}")

                results.append(
                    {
                        "tool_call_id": tool_call.get("id"),
                        "name": function_name,
                        "result": result,
                    }
                )

            except Exception as e:
                logger.error(f"Error executing tool call {function_name}: {str(e)}")
                results.append(
                    {
                        "tool_call_id": tool_call.get("id"),
                        "name": function_name,
                        "error": str(e),
                    }
                )

        # Final progress update
        await self._send_message(
            websocket,
            {
                "type": "typing",
                "message": f"Processing results... (Round {round_number} complete)",
                "timestamp": datetime.now().isoformat(),
            },
        )

        return results

    async def _execute_single_tool_call(self, tool_metadata: Dict, arguments: Dict, session=None) -> Any:
        """Execute a single tool call

        Args:
            tool_metadata: Tool metadata including path, method, and auth config
            arguments: Tool call arguments
            session: ChatSession with authentication info (optional)
        """

        method = tool_metadata["method"]
        path = tool_metadata["path"]

        # Build URL
        url = f"{self.app_base_url}{path}"

        # Substitute path parameters
        path_params = {}
        query_params = {}
        body_data = {}

        # Categorize arguments
        for arg_name, arg_value in arguments.items():
            if f"{{{arg_name}}}" in path:
                path_params[arg_name] = arg_value
                url = url.replace(f"{{{arg_name}}}", str(arg_value))
            else:
                if method in ["GET", "DELETE"]:
                    query_params[arg_name] = arg_value
                else:
                    body_data[arg_name] = arg_value

        # Prepare request
        request_kwargs = {
            "url": url,
            "params": query_params if query_params else None,
        }

        # Add body data for POST/PUT/PATCH
        if method in ["POST", "PUT", "PATCH"] and body_data:
            request_kwargs["json"] = body_data

        # Add headers
        request_kwargs["headers"] = {
            "Content-Type": "application/json",
            "User-Agent": "auto-bedrock-chat-fastapi/internal",
        }

        # Apply authentication if session has credentials configured
        if session and session.credentials and session.auth_handler:
            auth_type_str = session.credentials.get_auth_type_string()
            if auth_type_str != "none":
                try:
                    # Get tool-specific auth config from metadata
                    tool_auth_config = tool_metadata.get("_metadata", {}).get("authentication")

                    # Apply authentication to headers
                    request_kwargs["headers"] = await session.auth_handler.apply_auth_to_headers(
                        request_kwargs["headers"],
                        tool_auth_config,
                    )
                    logger.debug(f"Applied {auth_type_str} authentication to tool call")

                except Exception as e:
                    logger.error(f"Error applying authentication: {str(e)}")
                    return {"error": f"Authentication failed: {str(e)}"}

        # Make HTTP request
        try:
            if method == "GET":
                response = await self.http_client.get(**request_kwargs)
            elif method == "POST":
                response = await self.http_client.post(**request_kwargs)
            elif method == "PUT":
                response = await self.http_client.put(**request_kwargs)
            elif method == "PATCH":
                response = await self.http_client.patch(**request_kwargs)
            elif method == "DELETE":
                response = await self.http_client.delete(**request_kwargs)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            # Handle response
            if response.status_code >= 400:
                error_detail = response.text
                try:
                    error_json = response.json()
                    error_detail = error_json.get("detail", error_detail)
                except BaseException:
                    pass

                return {
                    "error": f"HTTP {response.status_code}: {error_detail}",
                    "status_code": response.status_code,
                }

            # Return successful response
            try:
                return response.json()
            except BaseException:
                return {"result": response.text, "status_code": response.status_code}

        except httpx.TimeoutException:
            return {"error": "Request timeout"}
        except httpx.RequestError as e:
            return {"error": f"Request failed: {str(e)}"}
        except Exception as e:
            return {"error": f"Unexpected error: {str(e)}"}

    async def _handle_tool_calls_recursively(
        self,
        session_id: str,
        initial_response: Dict[str, Any],
        tools_desc: Optional[Dict],
        websocket: WebSocket,
        session=None,
    ) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """
        Handle tool calls recursively until AI provides a final response without more tool calls.

        Args:
            session_id: The chat session ID
            initial_response: The initial AI response that may contain tool calls
            tools_desc: Available tools description
            websocket: WebSocket connection
            session: ChatSession object with authentication info

        Returns:
            Tuple of (final_response, all_tool_results)
        """
        current_response = initial_response
        all_tool_results = []
        round_count = 0
        max_rounds = self.config.max_tool_call_rounds

        # If no initial tool calls, return the response as-is
        if not current_response.get("tool_calls"):
            logger.debug("No tool calls in response, returning directly")
            return current_response, all_tool_results

        while current_response.get("tool_calls") and round_count < max_rounds:
            round_count += 1

            await self._send_message(
                websocket,
                {
                    "type": "typing",
                    "message": current_response.get("content", "Working on your request..."),
                    "timestamp": datetime.now().isoformat(),
                },
            )

            logger.debug(f"Tool call round {round_count}, processing {len(current_response['tool_calls'])} tool calls")

            # Add the assistant message with tool calls to history
            tool_assistant_message = ChatMessage(
                role="assistant",
                content=current_response.get("content", ""),
                tool_calls=current_response.get("tool_calls", []),
                metadata=current_response.get("metadata", {}),
            )
            await self.session_manager.add_message(session_id, tool_assistant_message)

            # Execute the tool calls
            tool_results = await self._execute_tool_calls(current_response["tool_calls"], session)
            all_tool_results.extend(tool_results)

            # Add tool results to context
            tool_message = ChatMessage(
                role="tool",
                content=f"Tool results (round {round_count})",
                tool_calls=current_response["tool_calls"],
                tool_results=tool_results,
            )
            await self.session_manager.add_message(session_id, tool_message)

            # Get updated context and make another request
            updated_context = await self.session_manager.get_context_messages(session_id)
            updated_bedrock_messages = self._format_messages_for_bedrock(updated_context)

            # Get next response from AI
            current_response = await self.bedrock_client.chat_completion(
                messages=updated_bedrock_messages,
                tools_desc=tools_desc,
                **self.config.get_bedrock_params(),
            )

            if current_response.get("tool_calls"):
                logger.debug(
                    f"AI requested {len(current_response['tool_calls'])} more tool calls in round {round_count + 1}"
                )
            else:
                logger.debug(f"AI provided final response after {round_count} tool call rounds")

        if round_count >= max_rounds and current_response.get("tool_calls"):
            logger.warning(f"Reached maximum tool call rounds ({max_rounds}), stopping recursion")
            # Add a note to the response about hitting the limit
            content = current_response.get("content", "")
            content += f"\n\n[Note: Reached maximum tool call limit of {max_rounds} rounds]"
            current_response["content"] = content
            current_response["tool_calls"] = []  # Stop further tool calls

        return current_response, all_tool_results

    def _format_messages_for_bedrock(self, messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        """Convert chat messages to Bedrock API format"""

        bedrock_messages = []

        # Check if system message is already present
        has_system_message = any(msg.role == "system" for msg in messages)

        # Add system prompt as first message if not present
        if not has_system_message:
            bedrock_messages.append({"role": "system", "content": self.config.get_system_prompt()})

        # Determine if we're using Claude or OpenAI GPT format
        is_claude_model = self.config.model_id.startswith("anthropic.claude") or self.config.model_id.startswith(
            "us.anthropic.claude"
        )

        for msg in messages:
            # Only include valid Bedrock message roles and content
            if msg.role in ["user", "assistant", "system"]:
                bedrock_msg = {"role": msg.role, "content": msg.content}
                bedrock_messages.append(bedrock_msg)

            # Handle tool result messages - format based on model type
            elif msg.role == "tool" and msg.tool_results:
                if is_claude_model:
                    # Claude format: structured content with tool_use and
                    # tool_result
                    self._add_claude_tool_messages(bedrock_messages, msg)
                else:
                    # OpenAI GPT format: assistant message with tool_calls +
                    # tool messages
                    self._add_gpt_tool_messages(bedrock_messages, msg)

        return bedrock_messages

    def _add_claude_tool_messages(self, bedrock_messages: List[Dict], msg: ChatMessage):
        """Add tool messages in Claude format"""

        # First, add the assistant message that made the tool calls
        if msg.tool_calls:
            # Create content array for assistant message with tool calls
            assistant_content = []

            # Add any text content if present
            if hasattr(msg, "content") and msg.content and msg.content.strip():
                assistant_content.append({"type": "text", "text": msg.content})

            # Add tool use blocks
            for tool_call in msg.tool_calls:
                assistant_content.append(
                    {
                        "type": "tool_use",
                        "id": tool_call.get("id"),
                        "name": tool_call.get("name"),
                        "input": tool_call.get("arguments", {}),
                    }
                )

            if assistant_content:
                bedrock_messages.append({"role": "assistant", "content": assistant_content})

        # Now add the user message with tool results
        tool_result_content = []
        for i, tool_result in enumerate(msg.tool_results):
            tool_call_id = msg.tool_calls[i].get("id") if i < len(msg.tool_calls) else f"tool_call_{i}"

            if "error" in tool_result:
                # Tool error result
                tool_result_content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": f"Error: {tool_result['error']}",
                    }
                )
            else:
                # Successful tool result
                result_text = str(tool_result.get("result", "No result"))
                tool_result_content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": result_text,
                    }
                )

        if tool_result_content:
            bedrock_messages.append({"role": "user", "content": tool_result_content})

    def _add_gpt_tool_messages(self, bedrock_messages: List[Dict], msg: ChatMessage):
        """Add tool messages in OpenAI GPT format"""

        # First, add the assistant message that made the tool calls
        if msg.tool_calls:
            # Convert tool calls to GPT format
            gpt_tool_calls = []
            for tool_call in msg.tool_calls:
                gpt_tool_calls.append(
                    {
                        "id": tool_call.get("id"),
                        "type": "function",
                        "function": {
                            "name": tool_call.get("name"),
                            "arguments": json.dumps(tool_call.get("arguments", {})),
                        },
                    }
                )

            assistant_msg = {
                "role": "assistant",
                "content": msg.content if msg.content else None,
            }

            # Only add tool_calls if there are any
            if gpt_tool_calls:
                assistant_msg["tool_calls"] = gpt_tool_calls

            bedrock_messages.append(assistant_msg)

        # Add individual tool result messages
        for i, tool_result in enumerate(msg.tool_results):
            tool_call_id = msg.tool_calls[i].get("id") if i < len(msg.tool_calls) else f"tool_call_{i}"

            if "error" in tool_result:
                content = f"Error: {tool_result['error']}"
            else:
                content = str(tool_result.get("result", "No result"))

            bedrock_messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": content})

    async def _handle_ping(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle ping message"""

        await self._send_message(websocket, {"type": "pong", "timestamp": datetime.now().isoformat()})

    async def _handle_history_request(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle history request"""

        session = await self.session_manager.get_session(websocket)
        if not session:
            await self._send_error(websocket, "Session not found")
            return

        history = await self.session_manager.get_conversation_history(session.session_id)

        await self._send_message(
            websocket,
            {
                "type": "history",
                "messages": [msg.to_dict() for msg in history],
                "timestamp": datetime.now().isoformat(),
            },
        )

    async def _handle_clear_history(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle clear history request"""

        session = await self.session_manager.get_session(websocket)
        if not session:
            await self._send_error(websocket, "Session not found")
            return

        # Clear conversation history but keep system message if present
        if session.conversation_history and session.conversation_history[0].role == "system":
            system_msg = session.conversation_history[0]
            session.conversation_history = [system_msg]
        else:
            session.conversation_history = []

        await self._send_message(
            websocket,
            {
                "type": "history_cleared",
                "message": "Conversation history cleared",
                "timestamp": datetime.now().isoformat(),
            },
        )

    async def _handle_auth_message(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle authentication message from client"""

        session = await self.session_manager.get_session(websocket)
        if not session:
            await self._send_error(websocket, "Session not found")
            return

        try:
            # Extract credentials from message
            auth_type = data.get("auth_type", "bearer_token").lower()

            # Create credentials based on auth type
            credentials = None

            if auth_type == "bearer_token":
                token = data.get("token")
                if not token:
                    await self._send_error(websocket, "Bearer token required")
                    return
                credentials = Credentials(
                    auth_type=AuthType.BEARER_TOKEN,
                    bearer_token=token,
                )

            elif auth_type == "basic_auth":
                username = data.get("username")
                password = data.get("password")
                if not username or not password:
                    await self._send_error(websocket, "Username and password required for basic auth")
                    return
                credentials = Credentials(
                    auth_type=AuthType.BASIC_AUTH,
                    username=username,
                    password=password,
                )

            elif auth_type == "api_key":
                api_key = data.get("api_key")
                api_key_header = data.get("api_key_header", "X-API-Key")
                if not api_key:
                    await self._send_error(websocket, "API key required")
                    return
                credentials = Credentials(
                    auth_type=AuthType.API_KEY,
                    api_key=api_key,
                    api_key_header=api_key_header,
                )

            elif auth_type == "oauth2" or auth_type == "oauth2_client_credentials":
                client_id = data.get("client_id")
                client_secret = data.get("client_secret")
                token_url = data.get("token_url")
                scope = data.get("scope")

                if not client_id or not client_secret or not token_url:
                    await self._send_error(websocket, "client_id, client_secret, and token_url required for OAuth2")
                    return

                credentials = Credentials(
                    auth_type=AuthType.OAUTH2_CLIENT_CREDENTIALS,
                    client_id=client_id,
                    client_secret=client_secret,
                    token_url=token_url,
                    scope=scope,
                )

            elif auth_type == "custom":
                custom_headers = data.get("custom_headers", {})
                credentials = Credentials(
                    auth_type=AuthType.CUSTOM,
                    custom_headers=custom_headers,
                    metadata=data.get("metadata", {}),
                )

            else:
                await self._send_error(websocket, f"Unknown auth type: {auth_type}")
                return

            # Validate credentials
            if not credentials:
                await self._send_error(websocket, "Failed to create credentials")
                return

            auth_handler = AuthenticationHandler(credentials)
            if not auth_handler.validate_credentials():
                await self._send_error(websocket, "Invalid credentials provided")
                return

            # Store credentials in session
            session.credentials = credentials
            session.auth_handler = auth_handler

            # Set HTTP client for OAuth2 if needed
            if auth_type == "oauth2" or auth_type == "oauth2_client_credentials":
                session.auth_handler.set_http_client(self.http_client)

            logger.info(f"Authentication configured for session {session.session_id}: {auth_type}")
            logger.debug(f"Session credentials: {credentials}")

            await self._send_message(
                websocket,
                {
                    "type": "auth_configured",
                    "message": f"Authentication configured: {auth_type}",
                    "auth_type": auth_type,
                    "timestamp": datetime.now().isoformat(),
                },
            )

        except Exception as e:
            logger.error(f"Error handling authentication message: {str(e)}")
            self._total_errors += 1
            await self._send_error(websocket, f"Authentication error: {str(e)}")

    async def _handle_logout(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle logout message from client"""

        session = await self.session_manager.get_session(websocket)
        if not session:
            await self._send_error(websocket, "Session not found")
            return

        try:
            # Clear credentials from session
            session.credentials = None
            session.auth_handler = None

            logger.info(f"User logged out from session {session.session_id}")

            await self._send_message(
                websocket,
                {
                    "type": "logout_success",
                    "message": "Successfully logged out",
                    "timestamp": datetime.now().isoformat(),
                },
            )

        except Exception as e:
            logger.error(f"Error handling logout: {str(e)}")
            self._total_errors += 1
            await self._send_error(websocket, f"Logout error: {str(e)}")

    async def _send_message(self, websocket: WebSocket, message: Dict[str, Any]):
        """Send message to WebSocket client"""

        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Failed to send message: {str(e)}")
            raise WebSocketError(f"Failed to send message: {str(e)}")

    async def _send_error(self, websocket: WebSocket, error_message: str):
        """Send error message to client"""

        await self._send_message(
            websocket,
            {
                "type": "error",
                "message": error_message,
                "timestamp": datetime.now().isoformat(),
            },
        )

    def _get_client_ip(self, websocket: WebSocket) -> str:
        """Extract client IP address from WebSocket"""

        # Check for forwarded headers first
        forwarded_for = websocket.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        real_ip = websocket.headers.get("x-real-ip")
        if real_ip:
            return real_ip

        # Fallback to client host
        client = websocket.client
        return client.host if client else "unknown"

    def _create_error_response(self, error_message: str) -> str:
        """Create user-friendly error response"""

        if "timeout" in error_message.lower():
            return "I'm taking longer than usual to respond. Please try again."
        elif "rate limit" in error_message.lower():
            return "I'm receiving too many requests. Please wait a moment and try again."
        elif "access denied" in error_message.lower():
            return "I don't have access to that model or service. Please contact support."
        elif "model" in error_message.lower():
            return "I'm having trouble with the AI model. Please try again in a moment."
        else:
            return f"I encountered an error: {error_message}. Please try again."

    async def get_statistics(self) -> Dict[str, Any]:
        """Get WebSocket handler statistics"""

        session_stats = await self.session_manager.get_statistics()

        return {
            "websocket": {
                "total_messages_handled": self._total_messages_handled,
                "total_tool_calls_executed": self._total_tool_calls_executed,
                "total_errors": self._total_errors,
            },
            "sessions": session_stats,
            "tools": self.tools_generator.get_tool_statistics(),
        }

    async def shutdown(self):
        """Shutdown the WebSocket handler"""

        # Close HTTP client
        await self.http_client.aclose()

        # Shutdown session manager
        await self.session_manager.shutdown()

        logger.info("WebSocket handler shutdown complete")
