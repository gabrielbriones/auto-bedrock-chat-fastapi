# auto-bedrock-chat-fastapi

🤖 **Automatically add AI chat capabilities to your FastAPI application with Amazon Bedrock integration**

Transform your FastAPI app into an intelligent assistant that can interact with your API endpoints through natural language conversations. This plugin automatically generates AI tools from your OpenAPI specification and provides a real-time WebSocket chat interface powered by Amazon Bedrock.

[![PyPI version](https://badge.fury.io/py/auto-bedrock-chat-fastapi.svg)](https://badge.fury.io/py/auto-bedrock-chat-fastapi)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## ✨ Features

- 🚀 **Zero Configuration**: Add AI chat to your FastAPI app with just one decorator
- 🔧 **Automatic Tool Generation**: Converts your OpenAPI spec into AI-callable tools
- 💬 **Real-time Chat**: WebSocket-based chat interface with typing indicators
- 🧠 **Amazon Bedrock Integration**: Supports Claude 4.5, Claude 3.5, OpenAI GPT OSS, Titan, Llama, and other Bedrock models
- 🎨 **Built-in UI**: Optional web chat interface (no frontend needed)
- 🔒 **Configurable Security**: Control which endpoints are exposed to AI
- 📊 **Tool Execution**: AI can call your API endpoints and get real responses
- 🎯 **Type-Safe**: Full TypeScript-style hints and validation
- 🧠 **Smart Conversation Management**: Automatic conversation history trimming to prevent context length errors
- 📄 **Large Message Chunking**: Handles oversized messages (like log files) with intelligent splitting
- ⚡ **Recursive Tool Calls**: Supports complex multi-step problem-solving workflows

## 🚀 Quick Start

### Installation

> **Note**: This package is currently in development. Install directly from the GitHub repository:

```bash
# Install latest version from GitHub
pip install git+https://github.com/gabrielbriones/auto-bedrock-chat-fastapi.git

# Or install a specific version/branch
pip install git+https://github.com/gabrielbriones/auto-bedrock-chat-fastapi.git@main
```

Once officially released, it will be available via:
```bash
pip install auto-bedrock-chat-fastapi  # (Coming soon to PyPI)
```

#### Alternative Installation Methods

**For Development/Contributing:**
```bash
# Clone and install in editable mode
git clone https://github.com/gabrielbriones/auto-bedrock-chat-fastapi.git
cd auto-bedrock-chat-fastapi
pip install -e .
```

**Install with specific dependencies:**
```bash
# Install with development dependencies
pip install "git+https://github.com/gabrielbriones/auto-bedrock-chat-fastapi.git[dev]"

# Install with security dependencies  
pip install "git+https://github.com/gabrielbriones/auto-bedrock-chat-fastapi.git[security]"
```

**Requirements:**
- Python 3.9+
- FastAPI 0.100+
- AWS credentials configured

#### Verify Installation

```python
# Test the installation
import auto_bedrock_chat_fastapi
print(f"Successfully installed version: {auto_bedrock_chat_fastapi.__version__}")

# Quick test
from auto_bedrock_chat_fastapi import add_bedrock_chat
print("✅ auto-bedrock-chat-fastapi is ready to use!")
```

#### Quick Test Example

Create a simple test file to verify everything works:

```python
# test_installation.py
from fastapi import FastAPI
from auto_bedrock_chat_fastapi import add_bedrock_chat

app = FastAPI(title="Installation Test")

@app.get("/test")
async def test_endpoint():
    """Test endpoint to verify the plugin works"""
    return {"message": "Hello from FastAPI!", "status": "working"}

# Add AI chat capabilities (requires AWS credentials)
add_bedrock_chat(
    app,
    bedrock_model_id="anthropic.claude-3-5-haiku-20241022-v1:0",  # Fast model for testing
    aws_region="us-east-1"
)

if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting test server...")
    print("💬 Chat UI will be available at: http://localhost:8000/bedrock-chat/ui")
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

Run with: `python test_installation.py`

#### Installation Troubleshooting

**Common Issues:**

1. **Git not found**: Ensure Git is installed and accessible in your PATH
2. **Permission errors**: Try installing in a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install git+https://github.com/gabrielbriones/auto-bedrock-chat-fastapi.git
   ```
3. **Dependency conflicts**: Create a fresh virtual environment
4. **SSL/Certificate errors**: Try:
   ```bash
   pip install --trusted-host github.com git+https://github.com/gabrielbriones/auto-bedrock-chat-fastapi.git
   ```
5. **Access denied**: Ensure the repository is public or you have access rights

### Basic Usage

```python
from fastapi import FastAPI
from auto_bedrock_chat_fastapi import add_bedrock_chat

app = FastAPI(title="My API")

# Your existing API endpoints
@app.get("/users/{user_id}")
async def get_user(user_id: int):
    """Get user by ID"""
    return {"user_id": user_id, "name": f"User {user_id}"}

@app.post("/users")
async def create_user(name: str, email: str):
    """Create a new user"""
    return {"id": 123, "name": name, "email": email}

# Add AI chat capabilities after defining your routes
add_bedrock_chat(
    app,
    bedrock_model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",  # Latest Claude 4.5
    aws_region="us-east-1"
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### Access Your AI Chat

- **Chat UI**: http://localhost:8000/bedrock-chat/ui
- **WebSocket**: ws://localhost:8000/bedrock-chat/ws
- **API Docs**: http://localhost:8000/docs

## 🎯 How It Works

1. **Automatic Discovery**: Scans your FastAPI routes and OpenAPI specification
2. **Tool Generation**: Converts API endpoints into AI-callable tools with proper schemas
3. **Bedrock Integration**: Connects to Amazon Bedrock for natural language processing
4. **Real-time Execution**: AI can call your API endpoints and return results in conversation
5. **User Interface**: Provides a clean web interface for chatting with your API

## 🏗️ Technical Implementation

### Chat Session Manager

The plugin uses a sophisticated session management system to handle WebSocket connections and conversation history:

```python
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
import asyncio
import uuid

@dataclass
class ChatMessage:
    role: str  # 'user', 'assistant', 'system'
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    tool_calls: Optional[List[Dict]] = None
    tool_results: Optional[List[Dict]] = None

@dataclass
class ChatSession:
    session_id: str
    websocket: WebSocket
    conversation_history: List[ChatMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    user_id: Optional[str] = None
    metadata: Dict = field(default_factory=dict)

class ChatSessionManager:
    def __init__(self, max_sessions: int = 1000, session_timeout: int = 3600):
        self._sessions: Dict[str, ChatSession] = {}
        self._websocket_to_session: Dict[WebSocket, str] = {}
        self.max_sessions = max_sessions
        self.session_timeout = session_timeout
        
        # Start cleanup task
        asyncio.create_task(self._cleanup_expired_sessions())
    
    async def create_session(self, websocket: WebSocket, user_id: Optional[str] = None) -> str:
        """Create a new chat session for the WebSocket connection"""
        session_id = str(uuid.uuid4())
        
        # Clean up if we're at max capacity
        if len(self._sessions) >= self.max_sessions:
            await self._cleanup_oldest_sessions(1)
        
        session = ChatSession(
            session_id=session_id,
            websocket=websocket,
            user_id=user_id
        )
        
        self._sessions[session_id] = session
        self._websocket_to_session[websocket] = session_id
        
        return session_id
    
    async def get_session(self, websocket: WebSocket) -> Optional[ChatSession]:
        """Get session by WebSocket connection"""
        session_id = self._websocket_to_session.get(websocket)
        if session_id:
            session = self._sessions.get(session_id)
            if session:
                session.last_activity = datetime.now()
                return session
        return None
    
    async def add_message(self, session_id: str, message: ChatMessage):
        """Add a message to session conversation history"""
        if session := self._sessions.get(session_id):
            session.conversation_history.append(message)
            session.last_activity = datetime.now()
            
            # Limit conversation history to prevent memory issues
            if len(session.conversation_history) > 50:
                session.conversation_history = session.conversation_history[-40:]  # Keep last 40 messages
    
    async def get_conversation_history(self, session_id: str) -> List[ChatMessage]:
        """Get conversation history for a session"""
        if session := self._sessions.get(session_id):
            return session.conversation_history
        return []
    
    async def remove_session(self, websocket: WebSocket):
        """Remove session when WebSocket disconnects"""
        if session_id := self._websocket_to_session.pop(websocket, None):
            self._sessions.pop(session_id, None)
```

### WebSocket Chat Handler

The WebSocket endpoint manages real-time communication between users and the AI:

```python
from fastapi import WebSocket, WebSocketDisconnect
from auto_bedrock_chat_fastapi.bedrock_client import BedrockClient
from auto_bedrock_chat_fastapi.config import ChatConfig

class WebSocketChatHandler:
    def __init__(self, session_manager: ChatSessionManager, bedrock_client: BedrockClient, config: ChatConfig):
        self.session_manager = session_manager
        self.bedrock_client = bedrock_client
        self.config = config
    
    async def handle_connection(self, websocket: WebSocket):
        """Handle new WebSocket connection"""
        await websocket.accept()
        session_id = await self.session_manager.create_session(websocket)
        
        try:
            # Send welcome message
            await websocket.send_json({
                "type": "connection_established",
                "session_id": session_id,
                "message": "Connected to AI assistant"
            })
            
            # Listen for messages
            while True:
                data = await websocket.receive_json()
                await self.handle_message(websocket, data)
                
        except WebSocketDisconnect:
            await self.session_manager.remove_session(websocket)
        except Exception as e:
            await websocket.send_json({
                "type": "error",
                "message": f"Connection error: {str(e)}"
            })
    
    async def handle_message(self, websocket: WebSocket, data: Dict):
        """Process incoming chat message"""
        session = await self.session_manager.get_session(websocket)
        if not session:
            await websocket.send_json({"type": "error", "message": "Session not found"})
            return
        
        user_message = data.get("message", "")
        if not user_message.strip():
            return
        
        # Add user message to history
        user_chat_message = ChatMessage(role="user", content=user_message)
        await self.session_manager.add_message(session.session_id, user_chat_message)
        
        # Send typing indicator
        await websocket.send_json({"type": "typing", "message": "AI is thinking..."})
        
        try:
            # Get conversation history for context
            history = await self.session_manager.get_conversation_history(session.session_id)
            
            # Call Bedrock with configuration
            response = await self.bedrock_client.chat_completion(
                messages=self._format_messages_for_bedrock(history),
                model_id=self.config.model_id,
                tools_desc=self.config.tools_desc,
                system_prompt=self.config.system_prompt,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens
            )
            
            # Process AI response
            ai_message = ChatMessage(
                role="assistant",
                content=response.get("content", ""),
                tool_calls=response.get("tool_calls", []),
                tool_results=response.get("tool_results", [])
            )
            
            # Add AI response to history
            await self.session_manager.add_message(session.session_id, ai_message)
            
            # Send response to client
            await websocket.send_json({
                "type": "ai_response",
                "message": ai_message.content,
                "tool_calls": ai_message.tool_calls,
                "tool_results": ai_message.tool_results,
                "timestamp": ai_message.timestamp.isoformat()
            })
            
        except Exception as e:
            error_message = f"Error processing message: {str(e)}"
            await websocket.send_json({"type": "error", "message": error_message})
    
    def _format_messages_for_bedrock(self, history: List[ChatMessage]) -> List[Dict]:
        """Convert chat history to Bedrock API format"""
        bedrock_messages = []
        
        for msg in history:
            bedrock_msg = {
                "role": msg.role,
                "content": msg.content
            }
            
            if msg.tool_calls:
                bedrock_msg["tool_calls"] = msg.tool_calls
            if msg.tool_results:
                bedrock_msg["tool_results"] = msg.tool_results
                
            bedrock_messages.append(bedrock_msg)
        
        return bedrock_messages
```

### 🧠 Conversation Management & Message Chunking

The plugin includes sophisticated conversation management to handle context length limits and large messages automatically.

#### Conversation History Management

Prevents "Input too long" errors from lengthy conversation history:

```python
# Configure conversation history limits
add_bedrock_chat(
    app,
    max_conversation_messages=20,              # Keep last 20 messages
    conversation_strategy="sliding_window",    # How to trim history
    preserve_system_message=True              # Always keep system prompt
)
```

**Available Strategies:**

- **`sliding_window`** (default): Preserves system message + most recent messages
- **`truncate`**: Simple truncation keeping newest messages
- **`smart_prune`**: Removes tool messages first, prioritizes user/assistant conversation

#### Message Chunking for Large Content

Automatically handles oversized messages (like large tool responses or log files):

```python
# Configure message chunking
add_bedrock_chat(
    app,
    max_message_size=100000,           # ~100KB limit before chunking
    chunk_size=80000,                  # ~80KB per chunk
    chunking_strategy="preserve_context", # Smart boundary detection
    chunk_overlap=1000,                # Context overlap between chunks
    enable_message_chunking=True       # Enable/disable chunking
)
```

**Chunking Strategies:**

- **`preserve_context`** (default): Breaks on natural boundaries (paragraphs, sentences)
- **`simple`**: Basic character-based splitting with overlap
- **`semantic`**: Future NLP-based intelligent splitting

**Real-world Example:**

```python
# Before: This would fail with "Input too long"
large_log_response = {"role": "tool", "content": huge_log_file}  # 500KB

# After: Automatically becomes multiple messages
# [CHUNK 1/7] This message was too large and has been split into chunks...
# [CHUNK 2/7] ...continued content...
# [CHUNK 3/7] ...more content...
```

#### Environment Configuration

```bash
# Conversation Management
BEDROCK_MAX_CONVERSATION_MESSAGES=20
BEDROCK_CONVERSATION_STRATEGY=sliding_window
BEDROCK_PRESERVE_SYSTEM_MESSAGE=true

# Message Chunking  
BEDROCK_MAX_MESSAGE_SIZE=100000
BEDROCK_CHUNK_SIZE=80000
BEDROCK_CHUNKING_STRATEGY=preserve_context
BEDROCK_CHUNK_OVERLAP=1000
BEDROCK_ENABLE_MESSAGE_CHUNKING=true

# Recursive Tool Calls
BEDROCK_MAX_TOOL_CALL_ROUNDS=10
```

#### Benefits

✅ **Prevents Context Errors**: No more "Input too long" failures  
✅ **Handles Large Responses**: Log files, data dumps, large API responses  
✅ **Maintains Context**: Smart overlap and boundary detection  
✅ **Automatic Operation**: Works transparently with existing code  
✅ **Configurable**: Fine-tune limits for your specific use case  

### Configuration Management

The plugin reads and manages configuration from multiple sources:

```python
from pydantic import BaseSettings, Field
from typing import List, Dict, Optional
import os

class ChatConfig(BaseSettings):
    """Configuration for Bedrock Chat Plugin"""
    
    # Model Configuration
    model_id: str = Field(
        default="anthropic.claude-3-5-sonnet-20241022-v2:0",  # Code default (override in .env)
        env="BEDROCK_MODEL_ID",
        description="Bedrock model identifier"
    )
    
    temperature: float = Field(
        default=0.7,
        env="BEDROCK_TEMPERATURE", 
        ge=0.0,
        le=1.0,
        description="Sampling temperature for model responses"
    )
    
    max_tokens: int = Field(
        default=4096,
        env="BEDROCK_MAX_TOKENS",
        gt=0,
        description="Maximum tokens in model response"
    )
    
    # System Configuration
    system_prompt: Optional[str] = Field(
        default=None,
        env="BEDROCK_SYSTEM_PROMPT",
        description="Custom system prompt for the AI assistant"
    )
    
    # API Tools Configuration
    tools_desc: Optional[Dict] = Field(
        default_factory=dict,
        description="Auto-generated tools from FastAPI routes"
    )
    
    allowed_paths: List[str] = Field(
        default_factory=list,
        env="BEDROCK_ALLOWED_PATHS",
        description="Whitelist of API paths to expose as tools"
    )
    
    excluded_paths: List[str] = Field(
        default_factory=lambda: ["/bedrock-chat", "/docs", "/redoc", "/openapi.json"],
        env="BEDROCK_EXCLUDED_PATHS",
        description="Blacklist of API paths to exclude from tools"
    )
    
    # Session Configuration
    max_tool_calls: int = Field(
        default=10,
        env="BEDROCK_MAX_TOOL_CALLS",
        gt=0,
        description="Maximum tool calls per conversation turn"
    )
    
    max_tool_call_rounds: int = Field(
        default=10,
        env="BEDROCK_MAX_TOOL_CALL_ROUNDS",
        gt=0,
        description="Maximum rounds of recursive tool calls"
    )
    
    timeout: int = Field(
        default=30,
        env="BEDROCK_TIMEOUT",
        gt=0,
        description="Timeout for API calls in seconds"
    )
    
    # Conversation History Management
    max_conversation_messages: int = Field(
        default=20,
        env="BEDROCK_MAX_CONVERSATION_MESSAGES",
        gt=0,
        description="Maximum messages to keep in conversation history"
    )
    
    conversation_strategy: str = Field(
        default="sliding_window",
        env="BEDROCK_CONVERSATION_STRATEGY",
        description="Strategy for handling long conversations: 'sliding_window', 'truncate', 'smart_prune'"
    )
    
    preserve_system_message: bool = Field(
        default=True,
        env="BEDROCK_PRESERVE_SYSTEM_MESSAGE",
        description="Whether to always preserve the system message when trimming history"
    )
    
    # Message Chunking Configuration
    max_message_size: int = Field(
        default=100000,
        env="BEDROCK_MAX_MESSAGE_SIZE",
        gt=0,
        description="Maximum characters in a single message before chunking (~100KB)"
    )
    
    chunk_size: int = Field(
        default=80000,
        env="BEDROCK_CHUNK_SIZE",
        gt=0,
        description="Size of each chunk when splitting large messages (~80KB)"
    )
    
    chunking_strategy: str = Field(
        default="preserve_context",
        env="BEDROCK_CHUNKING_STRATEGY",
        description="Strategy for chunking large messages: 'simple', 'preserve_context', 'semantic'"
    )
    
    chunk_overlap: int = Field(
        default=1000,
        env="BEDROCK_CHUNK_OVERLAP",
        ge=0,
        description="Number of characters to overlap between chunks for context continuity"
    )
    
    enable_message_chunking: bool = Field(
        default=True,
        env="BEDROCK_ENABLE_MESSAGE_CHUNKING",
        description="Whether to enable automatic chunking of large messages"
    )
    
    # WebSocket Configuration
    max_sessions: int = Field(
        default=1000,
        env="BEDROCK_MAX_SESSIONS",
        gt=0,
        description="Maximum concurrent WebSocket sessions"
    )
    
    session_timeout: int = Field(
        default=3600,
        env="BEDROCK_SESSION_TIMEOUT",
        gt=0,
        description="Session timeout in seconds"
    )
    
    # AWS Configuration
    aws_region: str = Field(
        default="us-east-1",
        env="AWS_REGION",
        description="AWS region for Bedrock service"
    )
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
    
    def get_system_prompt(self) -> str:
        """Get effective system prompt"""
        if self.system_prompt:
            return self.system_prompt
        
        return f"""
        You are a helpful AI assistant that can interact with API endpoints.
        
        Available tools: {len(self.tools_desc)} API endpoints
        Model: {self.model_id}
        
        Guidelines:
        - Be helpful and explain what you're doing
        - Use tools when users request API operations
        - Provide clear, accurate responses
        - Handle errors gracefully
        """

def load_config(
    model_id: Optional[str] = None,
    temperature: Optional[float] = None,
    system_prompt: Optional[str] = None,
    **kwargs
) -> ChatConfig:
    """Load configuration with optional overrides"""
    
    # Start with environment/file configuration
    config = ChatConfig()
    
    # Apply any direct overrides
    if model_id:
        config.model_id = model_id
    if temperature is not None:
        config.temperature = temperature
    if system_prompt:
        config.system_prompt = system_prompt
    
    # Apply any additional kwargs
    for key, value in kwargs.items():
        if hasattr(config, key) and value is not None:
            setattr(config, key, value)
    
    return config
```

### Bedrock Client Integration

The `bedrock_client.chat_completion` function is called with the processed configuration:

```python
# Example of how the plugin calls bedrock_client
async def process_chat_request(self, user_message: str, session: ChatSession) -> Dict:
    """Process chat request through Bedrock"""
    
    # Get conversation context
    history = await self.session_manager.get_conversation_history(session.session_id)
    
    # Format messages (system prompt is included as first message if not present)
    formatted_messages = self._format_messages_for_bedrock(history + [
        ChatMessage(role="user", content=user_message)
    ])
    
    # Call Bedrock with current configuration
    response = await self.bedrock_client.chat_completion(
        messages=formatted_messages,            # Includes system prompt as first message
        model_id=self.config.model_id,          # e.g., "anthropic.claude-3-5-sonnet-20241022-v2:0"
        tools_desc=self.config.tools_desc,      # Auto-generated from FastAPI routes
        temperature=self.config.temperature,    # e.g., 0.7
        max_tokens=self.config.max_tokens,     # e.g., 4096
        timeout=self.config.timeout           # e.g., 30 seconds
    )
    
    return response
```

This architecture ensures:
- **Scalable session management** with automatic cleanup
- **Persistent conversation history** per WebSocket connection  
- **Flexible configuration** from environment variables, files, or direct parameters
- **Clean separation** between WebSocket handling, session management, and Bedrock API calls

### Tools Description Generation

The plugin automatically generates tool descriptions from your FastAPI routes:

```python
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from typing import Dict, List, Any
import inspect

class ToolsGenerator:
    def __init__(self, app: FastAPI, config: ChatConfig):
        self.app = app
        self.config = config
    
    def generate_tools_desc(self) -> Dict[str, Any]:
        """Generate tools description from FastAPI OpenAPI spec"""
        
        # Get OpenAPI specification
        openapi_schema = get_openapi(
            title=self.app.title,
            version=self.app.version,
            description=self.app.description,
            routes=self.app.routes,
        )
        
        tools_desc = {
            "type": "function",
            "functions": []
        }
        
        # Process each API endpoint
        for path, path_info in openapi_schema.get("paths", {}).items():
            
            # Skip excluded paths
            if self._should_exclude_path(path):
                continue
            
            # Only include allowed paths if specified
            if self.config.allowed_paths and not self._is_allowed_path(path):
                continue
            
            # Process each HTTP method
            for method, operation in path_info.items():
                if method.upper() in ["GET", "POST", "PUT", "DELETE", "PATCH"]:
                    
                    function_desc = self._create_function_description(
                        path=path,
                        method=method.upper(),
                        operation=operation
                    )
                    
                    if function_desc:
                        tools_desc["functions"].append(function_desc)
        
        return tools_desc
    
    def _create_function_description(self, path: str, method: str, operation: Dict) -> Dict:
        """Create function description for Bedrock tool calling"""
        
        # Generate function name
        operation_id = operation.get("operationId", f"{method.lower()}_{path.replace('/', '_').replace('{', '').replace('}', '')}")
        
        function_desc = {
            "name": operation_id,
            "description": self._get_function_description(operation, method, path),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            },
            "_metadata": {
                "http_method": method,
                "path": path,
                "original_operation": operation
            }
        }
        
        # Extract parameters from OpenAPI spec
        parameters = operation.get("parameters", [])
        request_body = operation.get("requestBody", {})
        
        # Process path parameters
        for param in parameters:
            if param.get("in") == "path":
                self._add_parameter_to_function(function_desc, param, required=True)
        
        # Process query parameters  
        for param in parameters:
            if param.get("in") == "query":
                self._add_parameter_to_function(function_desc, param, required=param.get("required", False))
        
        # Process request body
        if request_body:
            self._add_request_body_to_function(function_desc, request_body)
        
        return function_desc
    
    def _get_function_description(self, operation: Dict, method: str, path: str) -> str:
        """Generate human-readable function description"""
        
        # Use existing description or summary
        description = operation.get("description") or operation.get("summary")
        
        if description:
            return f"{description} (HTTP {method} {path})"
        
        # Generate description from method and path
        action_map = {
            "GET": "retrieve" if "{" in path else "list",
            "POST": "create",
            "PUT": "update", 
            "PATCH": "partially update",
            "DELETE": "delete"
        }
        
        action = action_map.get(method, method.lower())
        resource = path.split("/")[-1].replace("{", "").replace("}", "")
        
        return f"{action.title()} {resource} via {method} {path}"
    
    def _add_parameter_to_function(self, function_desc: Dict, param: Dict, required: bool = False):
        """Add parameter to function description"""
        
        param_name = param["name"]
        param_schema = param.get("schema", {})
        
        function_desc["parameters"]["properties"][param_name] = {
            "type": param_schema.get("type", "string"),
            "description": param.get("description", f"The {param_name} parameter")
        }
        
        # Add enum values if present
        if "enum" in param_schema:
            function_desc["parameters"]["properties"][param_name]["enum"] = param_schema["enum"]
        
        # Add to required list
        if required:
            function_desc["parameters"]["required"].append(param_name)
    
    def _should_exclude_path(self, path: str) -> bool:
        """Check if path should be excluded"""
        for excluded in self.config.excluded_paths:
            if path.startswith(excluded.rstrip("/")):
                return True
        return False
    
    def _is_allowed_path(self, path: str) -> bool:
        """Check if path is in allowed list"""
        if not self.config.allowed_paths:
            return True
        
        for allowed in self.config.allowed_paths:
            if path.startswith(allowed.rstrip("/")):
                return True
        return False

# Example of generated tools_desc for bedrock_client.chat_completion
"""
{
    "type": "function", 
    "functions": [
        {
            "name": "get_user",
            "description": "Get user by ID (HTTP GET /users/{user_id})",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "The user_id parameter"
                    }
                },
                "required": ["user_id"]
            },
            "_metadata": {
                "http_method": "GET",
                "path": "/users/{user_id}",
                "original_operation": {...}
            }
        },
        {
            "name": "create_user",
            "description": "Create a new user (HTTP POST /users)",
            "parameters": {
                "type": "object", 
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "User's full name"
                    },
                    "email": {
                        "type": "string",
                        "description": "User's email address"
                    }
                },
                "required": ["name", "email"]
            },
            "_metadata": {
                "http_method": "POST",
                "path": "/users",
                "original_operation": {...}
            }
        }
    ]
}
"""
```

## 📖 Advanced Usage

### Class-Based Configuration

```python
from fastapi import FastAPI
from auto_bedrock_chat_fastapi import BedrockChatPlugin

app = FastAPI()

# Advanced configuration
bedrock_chat = BedrockChatPlugin(
    app=app,
    bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
    aws_region="us-east-1",
    chat_endpoint="/ai-assistant",
    websocket_endpoint="/ai-assistant/ws",
    ui_endpoint="/ai-assistant/ui",
    enable_ui=True,
    allowed_paths=["/users", "/orders"],  # Only expose specific endpoints
    excluded_paths=["/admin", "/internal"],  # Exclude sensitive endpoints
    custom_system_prompt="You are a helpful e-commerce assistant.",
    max_tool_calls=5,
    timeout=30
)

@app.get("/users/{user_id}")
async def get_user(user_id: int):
    return {"user_id": user_id, "name": f"User {user_id}"}

@app.get("/orders/{order_id}")
async def get_order(order_id: int):
    return {"order_id": order_id, "status": "shipped"}
```

### Custom System Prompts

```python
@add_bedrock_chat(
    bedrock_model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
    custom_system_prompt="""
    You are an expert API assistant for an e-commerce platform.
    
    Available capabilities:
    - User management (create, read, update users)
    - Order processing (view orders, update status)
    - Product catalog (search, filter products)
    
    Always be helpful and explain what you're doing when calling API endpoints.
    If you encounter errors, suggest alternative approaches.
    """
)
def setup_ai_chat(app: FastAPI):
    """Configure AI chat for the FastAPI application"""
    return app

# Apply to your app
app = FastAPI(title="E-commerce API")
# ... define your routes ...
setup_ai_chat(app)
```

### Environment Configuration

```python
import os
from auto_bedrock_chat_fastapi import add_bedrock_chat

app = FastAPI(title="My API")

# Define your routes first
@app.get("/users/{user_id}")
async def get_user(user_id: int):
    return {"user_id": user_id, "name": f"User {user_id}"}

# Configure AI chat with environment variables
add_bedrock_chat(
    app,
    bedrock_model_id=os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
    aws_region=os.getenv("AWS_REGION", "us-east-1"),
    enable_ui=os.getenv("ENABLE_BEDROCK_UI", "true").lower() == "true",
    excluded_paths=os.getenv("BEDROCK_EXCLUDED_PATHS", "/admin,/health").split(",")
)
```

### Using OpenAI GPT OSS Model

```python
from fastapi import FastAPI
from auto_bedrock_chat_fastapi import add_bedrock_chat

app = FastAPI(title="My API with OpenAI GPT OSS")

# Your existing API endpoints
@app.get("/projects/{project_id}")
async def get_project(project_id: int):
    """Get project details"""
    return {"project_id": project_id, "name": f"Project {project_id}", "status": "active"}

@app.post("/projects")
async def create_project(name: str, description: str):
    """Create a new project"""
    return {"id": 456, "name": name, "description": description, "status": "created"}

# Configure with OpenAI GPT OSS 120B model
add_bedrock_chat(
    app,
    bedrock_model_id="openai.gpt-oss-120b-1:0",
    aws_region="us-east-1",
    custom_system_prompt="""
    You are a helpful project management assistant powered by OpenAI's GPT OSS model.
    
    You can help with:
    - Creating and managing projects
    - Retrieving project information
    - Analyzing project data
    
    Always provide clear, actionable responses and explain your reasoning.
    """,
    max_tool_calls=8,  # GPT OSS handles complex reasoning well
    timeout=45  # Allow more time for the 120B model processing
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

## 🔧 Configuration Options

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `bedrock_model_id` | str | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | Amazon Bedrock model ID (see supported models below) |
| `aws_region` | str | `us-east-1` | AWS region for Bedrock |
| `chat_endpoint` | str | `/bedrock-chat` | Base endpoint for chat API |
| `websocket_endpoint` | str | `/bedrock-chat/ws` | WebSocket endpoint |
| `ui_endpoint` | str | `/bedrock-chat/ui` | Web UI endpoint |
| `enable_ui` | bool | `True` | Enable built-in chat UI |
| `allowed_paths` | List[str] | `[]` | Whitelist of API paths (empty = all allowed) |
| `excluded_paths` | List[str] | `["/bedrock-chat", "/docs", "/redoc"]` | Blacklist of API paths |
| `custom_system_prompt` | str | `None` | Custom system prompt for AI |
| `max_tool_calls` | int | `10` | Maximum tool calls per conversation |
| `timeout` | int | `30` | Timeout for API calls (seconds) |

> **💡 Model Selection Tip**: For open-source focused deployments, consider using `openai.gpt-oss-120b-1:0` which provides excellent performance with transparent, open-source foundations.

## 🔐 AWS Setup

### 1. Install AWS CLI and Configure Credentials

```bash
# Install AWS CLI
pip install awscli

# Configure credentials
aws configure
```

### 2. Set Environment Variables

```bash
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key
export AWS_DEFAULT_REGION=us-east-1
```

### 3. Enable Bedrock Model Access

1. Go to AWS Console → Amazon Bedrock
2. Navigate to "Model access" in the left sidebar
3. Request access to your desired models (Claude, Titan, Llama, etc.)
4. Wait for approval (usually instant for most models)

## 🔒 Security Considerations

**CRITICAL**: This plugin exposes your API endpoints to AI models. Implement proper security measures:

### Authentication & Authorization

```python
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from auto_bedrock_chat_fastapi import add_bedrock_chat

security = HTTPBearer()

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify JWT token or API key"""
    if not verify_jwt_token(credentials.credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials"
        )
    return credentials

app = FastAPI()

# Add authentication dependency to chat endpoint
add_bedrock_chat(
    app,
    bedrock_model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
    aws_region="us-east-1",
    auth_dependency=verify_token,  # Require authentication for chat
    excluded_paths=["/admin", "/internal", "/sensitive-data"]
)
```

### Rate Limiting

```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Apply rate limiting to chat endpoints
add_bedrock_chat(
    app,
    rate_limiter=limiter,
    rate_limit="10/minute",  # 10 requests per minute per IP
    **other_config
)
```

### Input Validation

```python
from pydantic import BaseModel, validator
from typing import List

class ChatMessage(BaseModel):
    message: str
    session_id: str
    
    @validator('message')
    def validate_message(cls, v):
        if len(v) > 4000:  # Limit message length
            raise ValueError('Message too long')
        if any(word in v.lower() for word in ['system', 'admin', 'root']):
            raise ValueError('Suspicious content detected')
        return v

add_bedrock_chat(
    app,
    message_validator=ChatMessage,
    **other_config
)
```

### Production Security Checklist

- [ ] **Enable HTTPS only** in production
- [ ] **Implement proper authentication** (JWT, OAuth2, API keys)
- [ ] **Use rate limiting** to prevent abuse
- [ ] **Validate all inputs** and sanitize outputs
- [ ] **Exclude sensitive endpoints** from AI access
- [ ] **Monitor API usage** and set up alerts
- [ ] **Use environment variables** for secrets
- [ ] **Enable CORS properly** for web clients
- [ ] **Implement request logging** for audit trails
- [ ] **Set up AWS IAM roles** with minimal permissions

## 🎨 Chat Interface

The built-in chat interface provides:

- ✅ Real-time messaging with WebSocket connection
- ✅ Typing indicators when AI is processing
- ✅ Tool call visualization showing which APIs were called
- ✅ Error handling with user-friendly messages
- ✅ Conversation history maintained during session
- ✅ Responsive design works on desktop and mobile

### Custom Frontend Integration

You can also integrate with your own frontend using the WebSocket API:

```javascript
const ws = new WebSocket('ws://localhost:8000/bedrock-chat/ws');

ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    
    if (data.type === 'ai_response') {
        console.log('AI:', data.message);
        console.log('Tool calls:', data.tool_calls);
    }
};

// Send message
ws.send(JSON.stringify({
    message: "Get user with ID 123",
    history: []
}));
```

## 🛠️ Supported Bedrock Models

| Model Family | Model ID | Description |
|--------------|----------|-------------|
| **Claude 4.5 Sonnet** | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | **🆕 Latest and most advanced (Sept 2025)** |
| Claude 3.5 Sonnet (Inference Profile) | `us.anthropic.claude-3-5-sonnet-20241022-v2:0` | Optimized throughput version (Oct 2024) |
| Claude 3.5 Sonnet | `anthropic.claude-3-5-sonnet-20241022-v2:0` | Latest and most capable (Oct 2024) |
| Claude 3.5 Sonnet | `anthropic.claude-3-5-sonnet-20240620-v1:0` | Previous version (June 2024) |
| Claude 3.5 Haiku | `anthropic.claude-3-5-haiku-20241022-v1:0` | Fast and cost-effective (Oct 2024) |
| Claude 3 Opus | `anthropic.claude-3-opus-20240229-v1:0` | Most capable, slower |
| Claude 3 Sonnet | `anthropic.claude-3-sonnet-20240229-v1:0` | Legacy - use 3.5 instead |
| Claude 3 Haiku | `anthropic.claude-3-haiku-20240307-v1:0` | Legacy - use 3.5 instead |
| **OpenAI GPT OSS** | `openai.gpt-oss-120b-1:0` | **120B parameter open-source model** |
| Titan Text | `amazon.titan-text-express-v1` | Amazon's foundation model ⚠️ **No tool calling** |
| **Llama 3.2 90B** | `us.meta.llama3-2-90b-instruct-v1:0` | **🆕 Latest Llama, excellent for tool calling (cross-region)** |
| Llama 3.1 70B | `meta.llama3-1-70b-instruct-v1:0` | Meta's open-source model, good for structured outputs |
| Llama 3.1 8B | `meta.llama3-1-8b-instruct-v1:0` | Smaller, faster version |
| Cohere Command R+ | `cohere.command-r-plus-v1:0` | Advanced reasoning model |
| Mistral Large | `mistral.mistral-large-2402-v1:0` | Multilingual capabilities |

### � Tool Calling Support

**✅ Models with Full Tool Calling Support:**
- **Claude Models**: All versions (`anthropic.claude-*`, `us.anthropic.claude-*`)
- **Llama Models**: All versions (`meta.llama*`, `us.meta.llama*`)  
- **OpenAI GPT OSS**: All versions (`openai.gpt-oss-*`)
- **Cohere Command**: All versions (`cohere.command-*`)

**⚠️ Models with Limited/No Tool Calling:**
- **Amazon Titan**: Text generation only, cannot call API endpoints
- **Mistral**: Limited tool calling support (check model-specific docs)

> **💡 For API Integration**: If you need the AI to call your FastAPI endpoints and retrieve real data, choose a model with full tool calling support. Titan models can only provide text responses and suggest which endpoints to call manually.

### �🔥 Model Recommendations

- **🚀 Best Overall**: `us.anthropic.claude-sonnet-4-5-20250929-v1:0` - Latest Claude 4.5 with advanced reasoning
- **� Best for Tool Calling**: `us.meta.llama3-2-90b-instruct-v1:0` - Llama 3.2 90B, excellent structured outputs
- **�💰 Cost-Effective**: `anthropic.claude-3-5-haiku-20241022-v1:0` - Fast responses, lower cost
- **🌐 Open Source**: `openai.gpt-oss-120b-1:0` - Transparent, OSS-based model
- **⚡ High Throughput**: `us.anthropic.claude-3-5-sonnet-20241022-v2:0` - Inference profile optimized

> **⚠️ Important Parameter Usage**: 
> - **Claude Models**: Use **either** `temperature` **or** `top_p` parameter, not both, to avoid validation errors
> - **OpenAI GPT Models**: Support both `temperature` **and** `top_p` parameters simultaneously
> - **Other Models**: Check model-specific documentation for parameter compatibility

## 🚀 New Model Support

### Claude 4.5 Sonnet (Latest)

Claude 4.5 Sonnet represents the most advanced version of Anthropic's language model, offering:

- **Enhanced Reasoning**: Superior performance on complex logical tasks
- **Improved Code Generation**: Better programming assistance and debugging
- **Advanced Tool Calling**: More reliable API endpoint interactions
- **Extended Context**: Better handling of long conversations and documents

```python
# Use Claude 4.5 with inference profile for optimal performance
add_bedrock_chat(
    app,
    bedrock_model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    aws_region="us-east-1",
    temperature=0.7  # Claude: Use temperature OR top_p, not both
)
```

### OpenAI GPT OSS (Open Source)

The `openai.gpt-oss-120b-1:0` model provides enterprise-grade performance with open-source transparency:

- **120B Parameters**: Large-scale model with excellent capabilities
- **Open Source Foundation**: Transparent model architecture and training
- **Cost Effective**: Competitive pricing for high-quality responses
- **API Compatibility**: Works seamlessly with all plugin features
- **Parameter Flexibility**: Supports both `temperature` and `top_p` parameters

```python
# Use OpenAI GPT OSS for open-source focused deployments
add_bedrock_chat(
    app,
    bedrock_model_id="openai.gpt-oss-120b-1:0",
    aws_region="us-east-1",
    temperature=0.7,
    top_p=0.9  # GPT models support both parameters simultaneously
)
```

### Model Selection Guidelines

Choose the right model for your use case:

```python
# For maximum capability and latest features
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0

# For best tool calling and structured outputs
BEDROCK_MODEL_ID=us.meta.llama3-2-90b-instruct-v1:0

# For cost-effective, fast responses
BEDROCK_MODEL_ID=anthropic.claude-3-5-haiku-20241022-v1:0

# For open-source transparency
BEDROCK_MODEL_ID=openai.gpt-oss-120b-1:0

# For high-throughput production (inference profile)
BEDROCK_MODEL_ID=us.anthropic.claude-3-5-sonnet-20241022-v2:0
```

### Model Validation

The plugin automatically validates model IDs to ensure compatibility. Supported model families:

- `anthropic.claude*` - Claude models (all versions)
- `us.anthropic.claude*` - Claude inference profiles (cross-region)
- `openai.gpt*` - OpenAI GPT OSS models  
- `amazon.titan*` - Amazon Titan models
- `meta.llama*` - Meta Llama models (direct access)
- `us.meta.llama*` - Meta Llama inference profiles (cross-region)
- `cohere.command*` - Cohere Command models
- `ai21.j2*` - AI21 Jurassic models
- `test-*` or `custom-*` - Testing/custom model overrides

### Environment Configuration

The plugin automatically detects your environment and loads the appropriate configuration file:

- **Development/Production**: Uses `.env` file
- **Testing**: Automatically uses `.env.test` when running under pytest
- **Custom**: Set `ENVIRONMENT=test` to force test environment

```python
# The plugin automatically chooses the right .env file
from auto_bedrock_chat_fastapi.config import load_config

config = load_config()  # Loads .env or .env.test automatically
```

**Environment Detection Triggers**:
- `PYTEST_CURRENT_TEST` environment variable
- `pytest` in executable path
- `pytest` in virtual environment path  
- `ENVIRONMENT=test` explicitly set

## 📝 Example Conversations

### Basic API Interactions

**User:** "Can you show me user 123?"  
**AI:** "I'll get the user information for you." *[Calls GET /users/123]*  
**AI:** "Here's the information for user 123: Name is 'User 123', email is 'user123@example.com', created on 2024-01-15."

**User:** "Create a new user named John with email john@example.com"  
**AI:** "I'll create a new user with that information." *[Calls POST /users with {name: "John", email: "john@example.com"}]*  
**AI:** "Successfully created user John! The new user ID is 456 and the account was created just now."

### Complex Multi-Step Operations

**User:** "Find all orders from last week and update any pending ones to 'processing'"  
**AI:** "I'll help you with that. Let me first get the orders from last week." *[Calls GET /orders?date_range=last_week]*  
**AI:** "I found 15 orders from last week. Now I'll check which ones are still pending." *[Analyzes results]*  
**AI:** "I found 3 pending orders (IDs: 789, 790, 791). Let me update them to 'processing' status."  
*[Calls PUT /orders/789/status, PUT /orders/790/status, PUT /orders/791/status]*  
**AI:** "Done! I've updated 3 orders from pending to processing status. All other orders from last week were already completed or shipped."

## 🏗️ Real-World Use Cases

### E-commerce Platform

```python
from fastapi import FastAPI
from auto_bedrock_chat_fastapi import add_bedrock_chat

app = FastAPI(title="E-commerce API")

# Product management
@app.get("/products")
async def search_products(query: str = "", category: str = "", max_price: float = None):
    """Search products with filters"""
    # Implementation here
    return {"products": [...]}

@app.get("/products/{product_id}")
async def get_product(product_id: int):
    """Get detailed product information"""
    return {"id": product_id, "name": "Product Name", "price": 29.99}

# Order management
@app.post("/orders")
async def create_order(items: list, customer_id: int):
    """Create a new order"""
    return {"order_id": 123, "total": 89.97, "status": "confirmed"}

@app.get("/orders/{order_id}")
async def get_order(order_id: int):
    """Get order details and tracking"""
    return {"order_id": order_id, "status": "shipped", "tracking": "TRACK123"}

# Customer service
@app.post("/support/tickets")
async def create_support_ticket(customer_id: int, issue: str):
    """Create support ticket"""
    return {"ticket_id": 456, "status": "open"}

# Configure AI assistant
add_bedrock_chat(
    app,
    bedrock_model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",  # Latest Claude 4.5
    aws_region="us-east-1",
    custom_system_prompt="""
    You are a helpful e-commerce assistant. You can help customers:
    
    🛍️ **Shopping**: Search products, get details, compare items
    📦 **Orders**: Create orders, track shipments, check status
    🎧 **Support**: Create tickets, answer questions, resolve issues
    
    Always be friendly and explain what you're doing. If you need to access customer data,
    make sure to verify their identity first.
    """,
    excluded_paths=["/admin", "/internal", "/payments/process"]
)
```

**Example conversation:**
> **Customer:** "I want to buy a laptop under $1000"  
> **AI:** "I'll help you find laptops under $1000. Let me search our inventory." *[Calls GET /products?query=laptop&max_price=1000]*  
> **AI:** "I found 8 laptops under $1000. Here are the top 3: (...) Would you like details on any of these?"

### CRM System

```python
from fastapi import FastAPI
from auto_bedrock_chat_fastapi import add_bedrock_chat

app = FastAPI(title="CRM API")

# Lead management
@app.get("/leads")
async def get_leads(status: str = None, assigned_to: int = None):
    """Get leads with optional filters"""
    return {"leads": [...]}

@app.put("/leads/{lead_id}/status")
async def update_lead_status(lead_id: int, status: str, notes: str = ""):
    """Update lead status"""
    return {"lead_id": lead_id, "status": status}

# Customer interactions
@app.post("/interactions")
async def log_interaction(customer_id: int, type: str, notes: str):
    """Log customer interaction"""
    return {"interaction_id": 789}

# Sales pipeline
@app.get("/sales/pipeline")
async def get_sales_pipeline():
    """Get current sales pipeline"""
    return {"total_value": 250000, "deals_by_stage": {...}}

add_bedrock_chat(
    app,
    custom_system_prompt="""
    You are a CRM assistant for sales teams. You can help with:
    
    🎯 **Lead Management**: View, update, and qualify leads
    📞 **Customer Interactions**: Log calls, meetings, and emails  
    📊 **Sales Analytics**: Pipeline reports and performance metrics
    🤝 **Deal Tracking**: Monitor opportunities and close rates
    
    Always maintain professional communication and respect data privacy.
    """,
    allowed_paths=["/leads", "/interactions", "/sales", "/customers"],
    max_tool_calls=8
)
```

### API Management Platform

```python
from fastapi import FastAPI
from auto_bedrock_chat_fastapi import add_bedrock_chat

app = FastAPI(title="API Management")

# API analytics
@app.get("/apis/{api_id}/metrics")
async def get_api_metrics(api_id: str, period: str = "24h"):
    """Get API usage metrics"""
    return {"requests": 15420, "errors": 23, "avg_latency": "142ms"}

# User management
@app.get("/users/{user_id}/usage")
async def get_user_usage(user_id: str):
    """Get user API usage"""
    return {"api_calls": 1250, "quota_remaining": 8750}

# Health monitoring
@app.get("/health/apis")
async def get_api_health():
    """Get health status of all APIs"""
    return {"healthy": 45, "degraded": 2, "down": 0}

add_bedrock_chat(
    app,
    custom_system_prompt="""
    You are an API management assistant. You help platform administrators:
    
    📊 **Monitor Performance**: Check API metrics, uptime, and errors
    👥 **Manage Users**: View usage, update quotas, handle access
    🔧 **Troubleshoot Issues**: Diagnose problems and suggest solutions
    📈 **Generate Reports**: Create usage and performance summaries
    
    Provide clear, actionable insights for API operations.
    """,
    rate_limit="20/minute",  # Higher limit for admin operations
    timeout=45  # Longer timeout for complex queries
)
```

## 🔍 Troubleshooting

### Error Handling & Resilience

```python
from auto_bedrock_chat_fastapi import add_bedrock_chat, BedrockChatConfig
import logging

# Configure comprehensive error handling
config = BedrockChatConfig(
    bedrock_model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",  # Latest Claude 4.5
    aws_region="us-east-1",
    
    # Retry configuration
    max_retries=3,
    retry_delay=1.0,
    exponential_backoff=True,
    
    # Timeout settings
    bedrock_timeout=30,
    api_call_timeout=10,
    
    # Error handling
    fallback_model="anthropic.claude-3-5-haiku-20241022-v1:0",
    graceful_degradation=True,
    
    # Logging
    log_level=logging.INFO,
    log_api_calls=True,
    log_errors=True
)

add_bedrock_chat(app, config=config)
```

### Circuit Breaker Pattern

```python
from circuitbreaker import circuit

@circuit(failure_threshold=5, recovery_timeout=30)
def call_bedrock_with_circuit_breaker(prompt: str):
    """Call Bedrock with circuit breaker protection"""
    # This will fail fast if Bedrock is down
    return bedrock_client.invoke_model(prompt)

add_bedrock_chat(
    app,
    model_caller=call_bedrock_with_circuit_breaker,
    **other_config
)
```

### Custom Error Responses

```python
from auto_bedrock_chat_fastapi import BedrockChatPlugin

class CustomErrorHandler:
    def handle_bedrock_error(self, error: Exception) -> str:
        if "throttling" in str(error).lower():
            return "I'm experiencing high traffic. Please try again in a moment."
        elif "access_denied" in str(error).lower():
            return "I don't have access to that model. Please contact support."
        else:
            return "I'm having technical difficulties. Please try again later."
    
    def handle_api_error(self, endpoint: str, error: Exception) -> str:
        return f"I couldn't access the {endpoint} endpoint. The service might be temporarily unavailable."

error_handler = CustomErrorHandler()
add_bedrock_chat(
    app,
    error_handler=error_handler,
    **other_config
)
```

### Common Issues

#### 1. AWS Credentials Not Found

```bash
# Set credentials explicitly
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
export AWS_DEFAULT_REGION=us-east-1

# Or use AWS profile
export AWS_PROFILE=my-profile

# Or use IAM roles (recommended for EC2/ECS)
# No environment variables needed
```

#### 2. Bedrock Model Access Denied

1. Go to AWS Console → Bedrock → Model Access
2. Request access to your desired model
3. Wait for approval (usually instant)
4. Verify your AWS account has sufficient permissions

#### 3. Rate Limiting / Throttling

```python
# Configure retry logic for throttling
add_bedrock_chat(
    app,
    max_retries=5,
    retry_delay=2.0,
    exponential_backoff=True,
    jitter=True,  # Add randomness to retry timing
)
```

#### 4. WebSocket Connection Failed

- Check if port 8000 is available
- Verify firewall settings
- Ensure WebSocket endpoint is accessible
- Check for proxy/load balancer WebSocket support

#### 5. No Tools Generated

```python
# Debug tool generation
import logging
logging.getLogger("auto_bedrock_chat_fastapi").setLevel(logging.DEBUG)

add_bedrock_chat(
    app,
    excluded_paths=[],  # Remove exclusions temporarily
    allowed_paths=["/users"],  # Explicitly allow paths
    debug_mode=True,  # Enable detailed logging
)
```

#### 6. Model Not Responding

- Verify model ID is correct and available in your region
- Check AWS region matches model availability
- Ensure sufficient AWS credits/permissions
- Try a different model as fallback

**For OpenAI GPT OSS 120B model specifically:**
- Ensure your AWS account has access to the OpenAI models in Bedrock
- Note that this model may have longer response times due to its 120B parameter size
- Consider increasing timeout values: `timeout=60` for complex queries
- Monitor token usage as the 120B model may consume more tokens per request

#### 7. Tool Calls Not Working

**Check Model Compatibility:**
```python
# ✅ Models that support tool calling
BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0  # Claude
BEDROCK_MODEL_ID=us.meta.llama3-2-90b-instruct-v1:0         # Llama
BEDROCK_MODEL_ID=openai.gpt-oss-120b-1:0                    # OpenAI GPT OSS

# ❌ Models that DON'T support tool calling  
BEDROCK_MODEL_ID=amazon.titan-text-express-v1               # Titan (text only)
```

**Titan Model Behavior:**
- Amazon Titan models will inform users they cannot call APIs directly
- They will suggest which endpoints to call manually
- Switch to Claude, Llama, or OpenAI GPT models for automated tool calling

#### 8. Memory/Performance Issues

```python
# Configure resource limits
add_bedrock_chat(
    app,
    max_concurrent_requests=10,
    request_queue_size=50,
    tool_call_cache_size=100,
    conversation_history_limit=20,
)
```

### Debug Mode

```python
import logging
logging.basicConfig(level=logging.DEBUG)

@add_bedrock_chat(
    bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
    # ... other options
)
def create_app():
    return app
```

### Health Check Endpoint

```python
# Add a health check to verify Bedrock connectivity
@app.get("/bedrock-chat/health")
async def bedrock_health():
    return {"status": "healthy", "bedrock": "connected"}
```

## 🚀 Performance Tips

- **Choose the Right Model**: 
  - **Claude 3.5 Haiku**: Fastest response times, cost-effective
  - **Claude 3.5 Sonnet**: Balanced performance and capabilities
  - **Llama 3.2 90B**: Excellent tool calling and structured outputs
  - **OpenAI GPT OSS 120B**: Strong performance for open-source workloads
  - **Claude 3 Opus**: Most capable but slower
- **Limit Tool Calls**: Set `max_tool_calls` to prevent infinite loops
- **Filter Endpoints**: Use `allowed_paths` to expose only necessary endpoints
- **Set Timeouts**: Configure appropriate timeout values for your API response times
- **Cache Responses**: Consider implementing response caching for frequently called endpoints

## 🐳 Production Deployment

### Docker Configuration

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Set environment variables
ENV PYTHONPATH=/app
ENV AWS_DEFAULT_REGION=us-east-1

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/bedrock-chat/health || exit 1

# Run application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```yaml
# docker-compose.yml
version: '3.8'
services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
      - AWS_DEFAULT_REGION=us-east-1
      - BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0
      - ENABLE_BEDROCK_UI=true
    volumes:
      - ./logs:/app/logs
    restart: unless-stopped
    
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./ssl:/etc/nginx/ssl
    depends_on:
      - api
```

### Kubernetes Deployment

```yaml
# k8s-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bedrock-chat-api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: bedrock-chat-api
  template:
    metadata:
      labels:
        app: bedrock-chat-api
    spec:
      containers:
      - name: api
        image: gabrielbriones/auto-bedrock-chat-fastapi:latest
        ports:
        - containerPort: 8000
        env:
        - name: AWS_DEFAULT_REGION
          value: "us-east-1"
        - name: BEDROCK_MODEL_ID
          value: "anthropic.claude-3-5-sonnet-20241022-v2:0"
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "1Gi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /bedrock-chat/health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /bedrock-chat/health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: bedrock-chat-service
spec:
  selector:
    app: bedrock-chat-api
  ports:
  - port: 80
    targetPort: 8000
  type: LoadBalancer
```

### AWS ECS/Fargate

```json
{
  "family": "bedrock-chat-api",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "executionRoleArn": "arn:aws:iam::YOUR_ACCOUNT:role/ecsTaskExecutionRole",
  "taskRoleArn": "arn:aws:iam::YOUR_ACCOUNT:role/bedrockChatRole",
  "containerDefinitions": [
    {
      "name": "bedrock-chat",
      "image": "gabrielbriones/auto-bedrock-chat-fastapi:latest",
      "portMappings": [
        {
          "containerPort": 8000,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "AWS_DEFAULT_REGION",
          "value": "us-east-1"
        },
        {
          "name": "BEDROCK_MODEL_ID",
          "value": "anthropic.claude-3-5-sonnet-20241022-v2:0"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/bedrock-chat-api",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "healthCheck": {
        "command": [
          "CMD-SHELL",
          "curl -f http://localhost:8000/bedrock-chat/health || exit 1"
        ],
        "interval": 30,
        "timeout": 5,
        "retries": 3
      }
    }
  ]
}
```

### Monitoring & Observability

```python
# monitoring.py
from prometheus_client import Counter, Histogram, generate_latest
from fastapi import FastAPI, Response
import time

# Metrics
chat_requests = Counter('bedrock_chat_requests_total', 'Total chat requests')
chat_response_time = Histogram('bedrock_chat_response_seconds', 'Chat response time')
tool_calls = Counter('bedrock_tool_calls_total', 'Total tool calls', ['endpoint'])

@app.middleware("http")
async def add_monitoring(request, call_next):
    start_time = time.time()
    response = await call_next(request)
    
    if "/bedrock-chat" in str(request.url):
        chat_requests.inc()
        chat_response_time.observe(time.time() - start_time)
    
    return response

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type="text/plain")
```

### Environment Variables for Production

```bash
# .env.production
AWS_DEFAULT_REGION=us-east-1

# Model Configuration - Choose one:
# BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0    # Latest Claude 4.5 (best performance)
# BEDROCK_MODEL_ID=us.meta.llama3-2-90b-instruct-v1:0              # Llama 3.2 90B (excellent tool calling)
# BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0       # Claude 3.5 Sonnet (recommended)
# BEDROCK_MODEL_ID=anthropic.claude-3-5-haiku-20241022-v1:0        # Fast and cost-effective
# BEDROCK_MODEL_ID=openai.gpt-oss-120b-1:0                         # Open-source focused deployments

BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0
ENABLE_BEDROCK_UI=false  # Disable in production
BEDROCK_EXCLUDED_PATHS=/admin,/internal,/health,/metrics
MAX_TOOL_CALLS=5
TIMEOUT=30
RATE_LIMIT=10/minute
LOG_LEVEL=INFO
CORS_ORIGINS=https://yourdomain.com,https://app.yourdomain.com
```

### Load Balancing Configuration

```nginx
# nginx.conf
upstream bedrock_chat_backend {
    server api1:8000;
    server api2:8000;
    server api3:8000;
}

server {
    listen 80;
    server_name yourdomain.com;
    
    # WebSocket support
    location /bedrock-chat/ws {
        proxy_pass http://bedrock_chat_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }
    
    # API endpoints
    location / {
        proxy_pass http://bedrock_chat_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Scaling Recommendations

- **Horizontal Scaling**: Use 3-5 replicas behind a load balancer
- **Vertical Scaling**: 2-4 CPU cores, 4-8GB RAM per instance
- **Database**: Use Redis for session storage and caching
- **CDN**: CloudFront or similar for static assets
- **Auto-scaling**: Configure based on CPU/memory usage
- **Circuit Breakers**: Implement for Bedrock API calls
- **Connection Pooling**: For database and AWS connections

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

### Development Setup

```bash
# Clone the repository
git clone https://github.com/gabrielbriones/auto-bedrock-chat-fastapi.git
cd auto-bedrock-chat-fastapi

# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run linting
black .
flake8 .
```

### Running Tests

```bash
# Unit tests
pytest tests/unit/

# Integration tests (requires AWS credentials)
pytest tests/integration/

# All tests
pytest
```

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [FastAPI](https://fastapi.tiangolo.com/) for the amazing web framework
- [Amazon Bedrock](https://aws.amazon.com/bedrock/) for AI model hosting
- [Anthropic Claude](https://www.anthropic.com/) for powerful language models
- The open-source community for inspiration and feedback

## 📞 Support

- 📖 [Documentation](https://gabrielbriones.github.io/auto-bedrock-chat-fastapi/)
- 🐛 [Issue Tracker](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/issues)
- 💬 [Discussions](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/discussions)
- 📧 [Email Support](mailto:gabriel.briones.dev@gmail.com)
- 🌟 [Give us a star](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi) if this project helps you!

## 🗺️ Roadmap

- [ ] Support for more Bedrock models (Mistral, AI21, etc.)
- [ ] Built-in authentication and authorization
- [ ] Conversation persistence and history
- [ ] Custom tool definitions beyond OpenAPI
- [ ] Streaming responses for better UX
- [ ] Multi-language support
- [ ] Plugin system for custom integrations

---

**Made with ❤️ for the FastAPI and AWS community**

*Transform your APIs into intelligent assistants powered by Amazon Bedrock in minutes, not hours.*