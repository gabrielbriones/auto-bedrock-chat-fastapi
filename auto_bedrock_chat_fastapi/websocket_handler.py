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
                auth_type_str = session.credentials.get_auth_type_string() if session.credentials else "none"
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

            # Convert ChatMessage objects to dicts for bedrock formatting
            message_dicts = [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "tool_calls": msg.tool_calls if hasattr(msg, "tool_calls") and msg.tool_calls else [],
                    "tool_results": msg.tool_results if hasattr(msg, "tool_results") and msg.tool_results else [],
                }
                for msg in context_messages
            ]

            # RAG: Retrieve relevant KB context if enabled
            kb_context_text = None
            kb_results = None
            if self.config.enable_rag:
                kb_results = await self._retrieve_kb_context(user_message)
                if kb_results:
                    kb_context_text = self._format_kb_context(kb_results)
                    logger.info(f"RAG: Injecting {len(kb_results)} KB chunks into context")
                    logger.debug(f"RAG: KB context length: {len(kb_context_text)} chars")
                    logger.debug(f"RAG: KB context preview (first 300 chars):\n{kb_context_text[:300]}...")

            # Inject KB context into system message if available
            if kb_context_text:
                # Get the base system prompt
                base_system_prompt = self.config.get_system_prompt()
                # Prepend KB context to system prompt
                enhanced_system_prompt = f"{kb_context_text}\n\n{base_system_prompt}"
                logger.debug(f"RAG: Final system prompt length: {len(enhanced_system_prompt)} chars")
                logger.debug(
                    f"RAG: System prompt with KB context (first 500 chars):\n{enhanced_system_prompt[:500]}..."
                )

                # Add enhanced system message to the beginning of message_dicts
                # First, remove any existing system messages
                message_dicts = [msg for msg in message_dicts if msg.get("role") != "system"]
                # Insert the enhanced system prompt at the beginning
                message_dicts.insert(0, {"role": "system", "content": enhanced_system_prompt})

            # Convert to Bedrock format using BedrockClient
            bedrock_messages = self.bedrock_client.format_messages_for_bedrock(message_dicts)

            # Get tools description
            tools_desc = self.tools_generator.generate_tools_desc()

            # Call Bedrock with bedrock params
            bedrock_params = self.config.get_bedrock_params()

            response = await self.bedrock_client.chat_completion(
                messages=bedrock_messages,
                tools_desc=tools_desc,
                **bedrock_params,
            )

            # Process tool calls recursively if any
            (
                final_response,
                all_tool_results,
            ) = await self._handle_tool_calls_recursively(session.session_id, response, tools_desc, websocket, session)
            content = final_response.get("content") or ""  # Handle None content gracefully
            logger.debug(f"Bedrock response ({len(content):,} chars): {content[:100]}")

            # Add the final AI response to history (if not already added)
            if not final_response.get("tool_calls"):
                ai_message = ChatMessage(
                    role="assistant",
                    content=final_response.get("content") or "",  # Handle None content gracefully
                    tool_calls=[],
                    tool_results=all_tool_results,
                    metadata=final_response.get("metadata", {}),
                )
                await self.session_manager.add_message(session.session_id, ai_message)

            # Prepare response metadata with KB info if RAG was used
            response_metadata = final_response.get("metadata", {}).copy()
            if kb_results:
                response_metadata["kb_used"] = True
                response_metadata["kb_chunks"] = len(kb_results)
                response_metadata["kb_sources"] = [
                    {
                        "title": r.get("title"),
                        "source": r.get("source"),
                        "url": r.get("source_url"),
                        "score": r["similarity_score"],
                    }
                    for r in kb_results
                ]

            # Send response to client (use final response data)
            await self._send_message(
                websocket,
                {
                    "type": "ai_response",
                    "message": final_response.get("content") or "",  # Handle None content gracefully
                    "tool_calls": final_response.get("tool_calls", []),
                    "tool_results": all_tool_results,
                    "timestamp": datetime.now().isoformat(),
                    "metadata": response_metadata,
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
                # logger.debug(f"Tool call result for {function_name}: {result}")

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
                    "message": current_response.get("content") or "Working on your request...",  # Handle None content
                    "timestamp": datetime.now().isoformat(),
                },
            )

            logger.debug(f"Tool call round {round_count}, processing {len(current_response['tool_calls'])} tool calls")

            # Add assistant message with tool calls (before executing them)
            # This preserves the actual assistant reasoning in the conversation
            assistant_message = ChatMessage(
                role="assistant",
                content=current_response.get("content", ""),
                tool_calls=current_response["tool_calls"],
            )
            await self.session_manager.add_message(session_id, assistant_message)

            # Execute the tool calls
            tool_results = await self._execute_tool_calls(current_response["tool_calls"], session)
            all_tool_results.extend(tool_results)

            # Add tool results to session for all models
            # The message formatter (bedrock_client.format_messages_for_bedrock) will handle model-specific formatting
            tool_message = ChatMessage(
                role="tool",
                content=f"Tool results (round {round_count})",
                tool_calls=current_response["tool_calls"],
                tool_results=tool_results,
                metadata={"is_tool_result": True},
            )
            await self.session_manager.add_message(session_id, tool_message)

            # Get updated context and make another request
            updated_context = await self.session_manager.get_context_messages(session_id)
            updated_message_dicts = [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "tool_calls": msg.tool_calls if hasattr(msg, "tool_calls") and msg.tool_calls else [],
                    "tool_results": msg.tool_results if hasattr(msg, "tool_results") and msg.tool_results else [],
                }
                for msg in updated_context
            ]
            updated_bedrock_messages = self.bedrock_client.format_messages_for_bedrock(updated_message_dicts)

            # Get next response from AI
            current_response = await self.bedrock_client.chat_completion(
                messages=updated_bedrock_messages,
                tools_desc=tools_desc,
                **self.config.get_bedrock_params(),
            )

            # Check if response is just a placeholder (happens sometimes with Llama)
            response_content = current_response.get("content", "").strip()
            is_placeholder = response_content.startswith("Tool results (round")

            if is_placeholder and not current_response.get("tool_calls"):
                # This is an empty placeholder, force tool calling to continue or end
                logger.warning(
                    f"Received placeholder response '{response_content}' with no tool calls. "
                    f"This likely indicates Llama confusion. Ending conversation loop."
                )
                # Clear content and treat as final response
                current_response["content"] = ""
                break

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

            # Set HTTP client for OAuth2 if needed
            if auth_type == "oauth2" or auth_type == "oauth2_client_credentials":
                auth_handler.set_http_client(self.http_client)

            # Verify credentials against remote endpoint if configured
            if self.config.auth_verification_endpoint:
                verification_url = self.config.auth_verification_endpoint
                # Resolve relative paths (e.g. "/api/v1/auth/verify") against app base URL
                if verification_url.startswith("/"):
                    verification_url = f"{self.app_base_url}{verification_url}"
                logger.info(
                    f"Verifying credentials for session {session.session_id} against {verification_url}"
                )
                is_valid, message = await auth_handler.verify_credentials_remote(
                    verification_url, http_client=self.http_client
                )
                if not is_valid:
                    await self._send_message(
                        websocket,
                        {
                            "type": "auth_failed",
                            "message": message,
                            "auth_type": auth_type,
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                    return

            # Store credentials in session
            session.credentials = credentials
            session.auth_handler = auth_handler

            logger.info(f"Authentication configured for session {session.session_id}: {auth_type}")
            logger.debug(
                "Session credentials configured for session %s: auth_type=%s, has_credentials=%s",
                session.session_id,
                auth_type,
                bool(credentials),
            )

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

            # Clear conversation history so new auth context is fresh
            session.conversation_history = []

            logger.info(f"User logged out from session {session.session_id}")

            # Try to send logout_success, but don't fail if the client closed the connection
            try:
                await self._send_message(
                    websocket,
                    {
                        "type": "logout_success",
                        "message": "Successfully logged out",
                        "timestamp": datetime.now().isoformat(),
                    },
                )
            except Exception as send_error:
                # Client may have already closed the connection, which is fine
                logger.debug(f"Could not send logout_success (client may have closed connection): {str(send_error)}")

        except Exception as e:
            logger.error(f"Error handling logout: {str(e)}")
            self._total_errors += 1
            try:
                await self._send_error(websocket, f"Logout error: {str(e)}")
            except Exception:
                # Connection might be closed, ignore
                pass

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

    async def _retrieve_kb_context(self, query: str) -> Optional[List[Dict[str, Any]]]:
        """
        Retrieve relevant knowledge base chunks for the given query.

        Args:
            query: User's message/question

        Returns:
            List of KB chunks with metadata, or None if RAG is disabled or retrieval fails
        """
        # Skip if RAG is disabled
        if not self.config.enable_rag:
            return None

        try:
            from .vector_db import VectorDB

            # Initialize vector DB
            vector_db = VectorDB(self.config.kb_database_path)

            # Generate embedding for the query
            query_embedding = await self.bedrock_client.generate_embedding(
                text=query, model_id=self.config.kb_embedding_model
            )

            # Perform search using configured weights
            # (set kb_keyword_weight=0 for pure semantic, kb_semantic_weight=0 for pure keyword)
            search_mode = f"semantic={self.config.kb_semantic_weight}, keyword={self.config.kb_keyword_weight}"
            logger.debug(f"RAG search mode: {search_mode}")
            results = vector_db.hybrid_search(
                query=query,
                query_embedding=query_embedding,
                limit=self.config.kb_top_k_results,
                min_score=self.config.kb_similarity_threshold,
                filters=None,
                semantic_weight=self.config.kb_semantic_weight,
                keyword_weight=self.config.kb_keyword_weight,
            )

            vector_db.close()

            # Log with the actual threshold used
            logger.info(
                f"RAG retrieval: Found {len(results)} relevant chunks (threshold={self.config.kb_similarity_threshold})"
            )

            if results:
                logger.debug(f"Top result score: {results[0]['similarity_score']:.4f}")
                # Debug: Log each chunk's details with component scores
                for i, result in enumerate(results, 1):
                    title = result.get("title", "N/A")[:60]
                    content_preview = result["content"][:150].replace("\n", " ")
                    score = result["similarity_score"]
                    semantic = result.get("semantic_component", "N/A")
                    keyword = result.get("keyword_component", "N/A")
                    if isinstance(semantic, float) and isinstance(keyword, float):
                        logger.debug(
                            f"  Chunk {i}: [hybrid={score:.4f}] "
                            f"(semantic={semantic:.4f} × {self.config.kb_semantic_weight} "
                            f"+ keyword={keyword:.4f} × {self.config.kb_keyword_weight}) "
                            f"{title} - {content_preview}..."
                        )
                    else:
                        logger.debug(f"  Chunk {i}: [{score:.4f}] {title} - {content_preview}...")

            return results if results else None

        except Exception as e:
            logger.error(f"KB retrieval failed: {str(e)}")
            return None

    def _format_kb_context(self, kb_results: List[Dict[str, Any]]) -> str:
        """
        Format KB chunks for inclusion in system prompt.

        Args:
            kb_results: List of KB search results

        Returns:
            Formatted string with KB context
        """
        if not kb_results:
            return ""

        context_parts = ["RELEVANT KNOWLEDGE BASE CONTEXT:"]
        context_parts.append("=" * 60)

        for i, result in enumerate(kb_results, 1):
            context_parts.append(f"\n[Context {i}] (Relevance: {result['similarity_score']:.2f})")

            # Add source attribution
            if result.get("title"):
                context_parts.append(f"Title: {result['title']}")
            if result.get("source"):
                context_parts.append(f"Source: {result['source']}")
            if result.get("source_url"):
                context_parts.append(f"URL: {result['source_url']}")

            context_parts.append(f"\n{result['content']}\n")
            context_parts.append("-" * 60)

        context_parts.append("\nINSTRUCTIONS:")
        context_parts.append("- The context above is provided for your information only - the user cannot see it")
        context_parts.append("- Use the context to inform your response when relevant")
        context_parts.append("- When citing information from the context, reference the actual source Title and URL")
        context_parts.append(
            "  Example: 'According to [Article Title](URL)...' or 'As mentioned in the documentation...'"
        )
        context_parts.append(
            "- DO NOT use internal references like '[Context 1]' or '[Context N]' - these mean nothing to the user"
        )
        context_parts.append("- If the context is not relevant to the question, answer from your general knowledge")
        context_parts.append("- Always be accurate and acknowledge if you're unsure")
        context_parts.append("=" * 60)

        return "\n".join(context_parts)

    async def shutdown(self):
        """Shutdown the WebSocket handler"""

        # Close HTTP client
        await self.http_client.aclose()

        # Shutdown session manager
        await self.session_manager.shutdown()

        logger.info("WebSocket handler shutdown complete")
