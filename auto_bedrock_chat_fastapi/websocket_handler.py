"""WebSocket handler for real-time chat communication"""

import asyncio
import json
import logging
import httpx
from typing import Dict, List, Any, Optional
from fastapi import WebSocket, WebSocketDisconnect, HTTPException
from datetime import datetime

from .config import ChatConfig
from .session_manager import ChatSessionManager, ChatMessage
from .bedrock_client import BedrockClient
from .tools_generator import ToolsGenerator
from .exceptions import WebSocketError, BedrockClientError


logger = logging.getLogger(__name__)


class WebSocketChatHandler:
    """Handles WebSocket connections and chat communication"""
    
    def __init__(
        self,
        session_manager: ChatSessionManager,
        bedrock_client: BedrockClient,
        tools_generator: ToolsGenerator,
        config: ChatConfig,
        app_base_url: str = "http://localhost:8000"
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
                ip_address=ip_address
            )
            
            logger.info(f"WebSocket connected: session={session_id}, user={user_id}, ip={ip_address}")
            
            # Send welcome message
            await self._send_message(websocket, {
                "type": "connection_established",
                "session_id": session_id,
                "message": "Connected to AI assistant",
                "timestamp": datetime.now().isoformat()
            })
            
            # Main message handling loop
            await self._message_loop(websocket)
            
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected normally")
        except Exception as e:
            logger.error(f"WebSocket connection error: {str(e)}")
            self._total_errors += 1
            
            try:
                await self._send_error(websocket, f"Connection error: {str(e)}")
            except:
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
            # Add user message to history
            user_chat_message = ChatMessage(
                role="user",
                content=user_message,
                metadata={"source": "websocket"}
            )
            await self.session_manager.add_message(session.session_id, user_chat_message)
            
            # Send typing indicator
            await self._send_message(websocket, {
                "type": "typing",
                "message": "AI is thinking...",
                "timestamp": datetime.now().isoformat()
            })
            
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
                **self.config.get_bedrock_params()
            )
            
            # Process tool calls if any
            tool_results = []
            if response.get("tool_calls"):
                tool_results = await self._execute_tool_calls(response["tool_calls"])
                
                # If tool calls were made, get another response with the results
                if tool_results:
                    # Add tool results to context
                    tool_message = ChatMessage(
                        role="tool",
                        content="Tool results",
                        tool_calls=response["tool_calls"],
                        tool_results=tool_results
                    )
                    await self.session_manager.add_message(session.session_id, tool_message)
                    
                    # Get updated context
                    updated_context = await self.session_manager.get_context_messages(session.session_id)
                    updated_bedrock_messages = self._format_messages_for_bedrock(updated_context)
                    
                    # Get final response
                    response = await self.bedrock_client.chat_completion(
                        messages=updated_bedrock_messages,
                        tools_desc=tools_desc,
                        **self.config.get_bedrock_params()
                    )
            
            # Create AI response message
            ai_message = ChatMessage(
                role="assistant",
                content=response.get("content", ""),
                tool_calls=response.get("tool_calls", []),
                tool_results=tool_results,
                metadata=response.get("metadata", {})
            )
            
            # Add AI response to history
            await self.session_manager.add_message(session.session_id, ai_message)
            
            # Send response to client
            await self._send_message(websocket, {
                "type": "ai_response",
                "message": ai_message.content,
                "tool_calls": ai_message.tool_calls,
                "tool_results": tool_results,
                "timestamp": ai_message.timestamp.isoformat(),
                "metadata": ai_message.metadata
            })
            
        except Exception as e:
            logger.error(f"Error processing chat message: {str(e)}")
            self._total_errors += 1
            
            # Send error to user
            error_response = self._create_error_response(str(e))
            await self._send_message(websocket, {
                "type": "ai_response",
                "message": error_response,
                "error": True,
                "timestamp": datetime.now().isoformat()
            })
    
    async def _execute_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Execute tool calls by making HTTP requests to API endpoints"""
        
        results = []
        
        for tool_call in tool_calls[:self.config.max_tool_calls]:
            try:
                self._total_tool_calls_executed += 1
                
                function_name = tool_call.get("name")
                arguments = tool_call.get("arguments", {})
                
                # Get tool metadata
                tool_metadata = self.tools_generator.get_tool_metadata(function_name)
                if not tool_metadata:
                    results.append({
                        "tool_call_id": tool_call.get("id"),
                        "name": function_name,
                        "error": f"Unknown tool: {function_name}"
                    })
                    continue
                
                # Validate arguments
                if not self.tools_generator.validate_tool_call(function_name, arguments):
                    results.append({
                        "tool_call_id": tool_call.get("id"),
                        "name": function_name,
                        "error": "Invalid arguments"
                    })
                    continue
                
                # Execute tool call
                result = await self._execute_single_tool_call(tool_metadata, arguments)
                
                results.append({
                    "tool_call_id": tool_call.get("id"),
                    "name": function_name,
                    "result": result
                })
                
            except Exception as e:
                logger.error(f"Error executing tool call {function_name}: {str(e)}")
                results.append({
                    "tool_call_id": tool_call.get("id"),
                    "name": function_name,
                    "error": str(e)
                })
        
        return results
    
    async def _execute_single_tool_call(self, tool_metadata: Dict, arguments: Dict) -> Any:
        """Execute a single tool call"""
        
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
            "User-Agent": "auto-bedrock-chat-fastapi/internal"
        }
        
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
                except:
                    pass
                
                return {
                    "error": f"HTTP {response.status_code}: {error_detail}",
                    "status_code": response.status_code
                }
            
            # Return successful response
            try:
                return response.json()
            except:
                return {"result": response.text, "status_code": response.status_code}
                
        except httpx.TimeoutException:
            return {"error": "Request timeout"}
        except httpx.RequestError as e:
            return {"error": f"Request failed: {str(e)}"}
        except Exception as e:
            return {"error": f"Unexpected error: {str(e)}"}
    
    def _format_messages_for_bedrock(self, messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        """Convert chat messages to Bedrock API format"""
        
        bedrock_messages = []
        
        # Check if system message is already present
        has_system_message = any(msg.role == "system" for msg in messages)
        
        # Add system prompt as first message if not present
        if not has_system_message:
            bedrock_messages.append({
                "role": "system",
                "content": self.config.get_system_prompt()
            })
        
        for msg in messages:
            # Only include valid Bedrock message roles and content
            if msg.role in ["user", "assistant", "system"]:
                bedrock_msg = {
                    "role": msg.role,
                    "content": msg.content
                }
                bedrock_messages.append(bedrock_msg)
            
            # Handle tool result messages by converting them to user messages
            elif msg.role == "tool" and msg.tool_results:
                tool_content = "Tool execution results:\n"
                for tool_result in msg.tool_results:
                    name = tool_result.get("name", "unknown")
                    if "error" in tool_result:
                        tool_content += f"- {name}: Error - {tool_result['error']}\n"
                    else:
                        tool_content += f"- {name}: {tool_result.get('result', 'No result')}\n"
                
                bedrock_messages.append({
                    "role": "user",
                    "content": tool_content
                })
        
        return bedrock_messages
    
    async def _handle_ping(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle ping message"""
        
        await self._send_message(websocket, {
            "type": "pong",
            "timestamp": datetime.now().isoformat()
        })
    
    async def _handle_history_request(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle history request"""
        
        session = await self.session_manager.get_session(websocket)
        if not session:
            await self._send_error(websocket, "Session not found")
            return
        
        history = await self.session_manager.get_conversation_history(session.session_id)
        
        await self._send_message(websocket, {
            "type": "history",
            "messages": [msg.to_dict() for msg in history],
            "timestamp": datetime.now().isoformat()
        })
    
    async def _handle_clear_history(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle clear history request"""
        
        session = await self.session_manager.get_session(websocket)
        if not session:
            await self._send_error(websocket, "Session not found")
            return
        
        # Clear conversation history but keep system message if present
        if (session.conversation_history and 
            session.conversation_history[0].role == "system"):
            system_msg = session.conversation_history[0]
            session.conversation_history = [system_msg]
        else:
            session.conversation_history = []
        
        await self._send_message(websocket, {
            "type": "history_cleared",
            "message": "Conversation history cleared",
            "timestamp": datetime.now().isoformat()
        })
    
    async def _send_message(self, websocket: WebSocket, message: Dict[str, Any]):
        """Send message to WebSocket client"""
        
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Failed to send message: {str(e)}")
            raise WebSocketError(f"Failed to send message: {str(e)}")
    
    async def _send_error(self, websocket: WebSocket, error_message: str):
        """Send error message to client"""
        
        await self._send_message(websocket, {
            "type": "error",
            "message": error_message,
            "timestamp": datetime.now().isoformat()
        })
    
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
            "tools": self.tools_generator.get_tool_statistics()
        }
    
    async def shutdown(self):
        """Shutdown the WebSocket handler"""
        
        # Close HTTP client
        await self.http_client.aclose()
        
        # Shutdown session manager
        await self.session_manager.shutdown()
        
        logger.info("WebSocket handler shutdown complete")