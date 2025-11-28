"""Main plugin class and decorator function for auto-bedrock-chat-fastapi"""

import asyncio
import atexit
import html
import logging
import os
from contextlib import asynccontextmanager
from typing import Callable, Optional

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .bedrock_client import BedrockClient
from .config import ChatConfig, load_config, validate_config
from .exceptions import BedrockChatError
from .session_manager import ChatSessionManager
from .tools_generator import ToolsGenerator
from .websocket_handler import WebSocketChatHandler

logger = logging.getLogger(__name__)


def _setup_logging(config: ChatConfig):
    """Setup logging configuration based on ChatConfig"""

    # Don't reconfigure if already configured
    if logging.getLogger().handlers:
        return

    # Map string log levels to logging constants
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }

    log_level = level_map.get(config.log_level.upper(), logging.INFO)

    # Configure basic logging
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Set level for our specific loggers
    logging.getLogger("auto_bedrock_chat_fastapi").setLevel(log_level)

    # Suppress verbose logging from third-party libraries if enabled
    if config.suppress_third_party_logs:
        logging.getLogger("botocore").setLevel(logging.WARNING)
        logging.getLogger("botocore.hooks").setLevel(logging.WARNING)
        logging.getLogger("botocore.regions").setLevel(logging.WARNING)
        logging.getLogger("botocore.endpoint").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("httpcore.connection").setLevel(logging.WARNING)
        logging.getLogger("httpcore.http11").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.INFO)  # Keep INFO for httpx (less verbose)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)


class BedrockChatPlugin:
    """Main plugin class for integrating Bedrock chat with FastAPI"""

    def __init__(self, app: FastAPI, config: Optional[ChatConfig] = None, **config_overrides):
        self.app = app
        self.config = config or load_config(**config_overrides)

        # Setup logging configuration
        _setup_logging(self.config)

        # Validate configuration
        validate_config(self.config)

        # Initialize components
        self.session_manager = ChatSessionManager(self.config)
        self.bedrock_client = BedrockClient(self.config)
        self.tools_generator = ToolsGenerator(app=self.app, config=self.config)

        # Determine base URL for internal API calls (after tools_generator is created)
        self.app_base_url = self._determine_base_url()

        self.websocket_handler = WebSocketChatHandler(
            session_manager=self.session_manager,
            bedrock_client=self.bedrock_client,
            tools_generator=self.tools_generator,
            config=self.config,
            app_base_url=self.app_base_url,
        )

        # Setup templates for UI
        self.templates = None
        if self.config.enable_ui:
            self._setup_templates()

        # Setup routes
        self._setup_routes()

        # Setup shutdown handler
        self._setup_shutdown()

        logger.info(f"Bedrock Chat Plugin initialized with model: {self.config.model_id}")

    def _determine_base_url(self) -> str:
        """
        Determine base URL for internal API calls.

        Priority order:
        1. Explicit api_base_url configuration (recommended for production)
        2. OpenAPI spec servers[0].url (auto-detected from framework specs)
        3. Environment variables (HOST/PORT, SERVER_HOST/SERVER_PORT, etc.)
        4. Default fallback (http://localhost:8000)

        For production deployments, it's strongly recommended to explicitly
        configure the api_base_url parameter rather than relying on auto-detection.
        """

        # Priority 1: Explicit configuration
        if self.config.api_base_url:
            logger.info(f"Using configured API base URL: {self.config.api_base_url}")
            return self.config.api_base_url

        # Priority 2: Try to get from ToolsGenerator (OpenAPI spec servers)
        try:
            api_base_url = self.tools_generator.get_api_base_url()
            if api_base_url and api_base_url != "http://localhost:8000":
                logger.info(f"Using API base URL from OpenAPI spec: {api_base_url}")
                return api_base_url
        except Exception as e:
            logger.debug(f"Could not get base URL from tools generator: {e}")

        # Priority 3: Try to detect from environment or runtime
        detected_url = self._detect_runtime_base_url()
        if detected_url:
            logger.info(f"Detected runtime API base URL: {detected_url}")
            return detected_url

        # Priority 4: Default fallback
        logger.info("Using default API base URL: http://localhost:8000")
        return "http://localhost:8000"

    def _detect_runtime_base_url(self) -> Optional[str]:
        """
        Try to detect the base URL from runtime environment.

        This method only uses stable, documented approaches and relies primarily
        on environment variables. For production deployments, it's recommended
        to explicitly set the api_base_url configuration parameter.
        """

        # Priority 1: Check standard environment variables (recommended approach)
        host_env = os.getenv("HOST")
        port_env = os.getenv("PORT")
        if host_env is not None and port_env is not None:
            scheme = "https" if os.getenv("HTTPS", "").lower() in ("1", "true") else "http"
            logger.debug(f"Detected base URL from HOST/PORT env vars: {scheme}://{host_env}:{port_env}")
            return f"{scheme}://{host_env}:{port_env}"

        # Priority 2: Check common deployment environment variables
        # Many cloud platforms and deployment tools set these
        for env_vars in [
            ("SERVER_HOST", "SERVER_PORT"),
            ("APP_HOST", "APP_PORT"),
            ("WEB_HOST", "WEB_PORT"),
        ]:
            host = os.getenv(env_vars[0])
            port = os.getenv(env_vars[1])
            if host and port:
                scheme = "https" if os.getenv("HTTPS", "").lower() in ("1", "true") else "http"
                logger.debug(f"Detected base URL from {env_vars} env vars: {scheme}://{host}:{port}")
                return f"{scheme}://{host}:{port}"

        # No reliable detection method available
        logger.debug("Could not detect runtime base URL from environment variables")
        return None

    def _setup_templates(self):
        """Setup Jinja2 templates for UI and mount static files"""

        # Create templates directory if it doesn't exist
        template_dir = os.path.join(os.path.dirname(__file__), "templates")
        if not os.path.exists(template_dir):
            os.makedirs(template_dir)

        self.templates = Jinja2Templates(directory=template_dir)

        # Mount static files
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        if os.path.exists(static_dir):
            self.app.mount(f"{self.config.chat_endpoint}/static", StaticFiles(directory=static_dir), name="static")

    def _setup_routes(self):
        """Setup FastAPI routes for chat functionality"""

        # Health check endpoint
        @self.app.get(f"{self.config.chat_endpoint}/health")
        async def bedrock_health():
            """Health check for Bedrock chat service"""
            try:
                bedrock_health = await self.bedrock_client.health_check()
                stats = await self.websocket_handler.get_statistics()

                return JSONResponse(
                    {
                        "status": ("healthy" if bedrock_health["status"] == "healthy" else "degraded"),
                        "bedrock": bedrock_health,
                        "statistics": stats,
                        "config": {
                            "model_id": self.config.model_id,
                            "region": self.config.aws_region,
                            "ui_enabled": self.config.enable_ui,
                            "max_sessions": self.config.max_sessions,
                        },
                    }
                )
            except Exception as e:
                logger.error(f"Health check failed: {str(e)}")
                return JSONResponse({"status": "unhealthy", "error": str(e)}, status_code=503)

        # WebSocket endpoint
        @self.app.websocket(self.config.websocket_endpoint)
        async def websocket_chat(websocket: WebSocket, user_id: Optional[str] = None):
            """WebSocket endpoint for real-time chat"""

            # Apply authentication if configured
            if self.config.auth_dependency:
                try:
                    # Note: WebSocket authentication needs to be handled differently
                    # This is a simplified approach
                    pass
                except Exception:
                    await websocket.close(code=1008, reason="Authentication failed")
                    return

            await self.websocket_handler.handle_connection(websocket, user_id)

        # Chat UI endpoint
        if self.config.enable_ui:

            @self.app.get(self.config.ui_endpoint, response_class=HTMLResponse)
            async def chat_ui(request: Request):
                """Serve chat UI"""

                if not self.templates:
                    template_dir = os.path.join(os.path.dirname(__file__), "templates")
                    return HTMLResponse(
                        content="<html><body><h1>Chat UI Error</h1>"
                        f"<p>Template rendering is unavailable. The template system failed to initialize.</p>"
                        f"<p>Expected template directory: <code>{html.escape(template_dir)}</code></p>"
                        "<p>Please check that the templates directory exists, is readable, and contains the required files.</p>"
                        "</body></html>",
                        status_code=500,
                    )

                try:
                    # Get supported auth types from config
                    supported_auth_types = []
                    if self.config.enable_tool_auth:
                        supported_auth_types = self.config.supported_auth_types

                    return self.templates.TemplateResponse(
                        "chat.html",
                        {
                            "request": request,
                            "websocket_url": self.config.websocket_endpoint,
                            "auth_enabled": self.config.enable_tool_auth,
                            "require_tool_auth": self.config.require_tool_auth,
                            "supported_auth_types": supported_auth_types,
                            "ui_title": self.config.ui_title,
                            "model_id": self.config.model_id,
                            "ui_welcome_message": self.config.ui_welcome_message,
                            "app_title": self.app.title or "API",
                        },
                    )
                except Exception as e:
                    logger.error(f"Template rendering failed: {str(e)}")
                    return HTMLResponse(
                        content=f"<html><body><h1>Chat UI Error</h1>"
                        f"<p>Failed to render template: {html.escape(str(e))}</p>"
                        "</body></html>",
                        status_code=500,
                    )

        # Statistics endpoint
        @self.app.get(f"{self.config.chat_endpoint}/stats")
        async def chat_statistics():
            """Get chat statistics"""

            try:
                stats = await self.websocket_handler.get_statistics()
                return JSONResponse(stats)
            except Exception as e:
                logger.error(f"Failed to get statistics: {str(e)}")
                return JSONResponse({"error": str(e)}, status_code=500)

        # Tools information endpoint
        @self.app.get(f"{self.config.chat_endpoint}/tools")
        async def chat_tools():
            """Get available tools information"""

            try:
                tools_desc = self.tools_generator.generate_tools_desc()
                tools_metadata = self.tools_generator.get_all_tools_metadata()
                tools_stats = self.tools_generator.get_tool_statistics()

                return JSONResponse(
                    {
                        "tools_description": tools_desc,
                        "tools_metadata": tools_metadata,
                        "statistics": tools_stats,
                    }
                )
            except Exception as e:
                logger.error(f"Failed to get tools info: {str(e)}")
                return JSONResponse({"error": str(e)}, status_code=500)

        logger.info("Chat routes setup complete:")
        logger.info(f"  WebSocket: {self.config.websocket_endpoint}")
        logger.info(f"  Health: {self.config.chat_endpoint}/health")
        logger.info(f"  Stats: {self.config.chat_endpoint}/stats")
        logger.info(f"  Tools: {self.config.chat_endpoint}/tools")
        if self.config.enable_ui:
            logger.info(f"  UI: {self.config.ui_endpoint}")

    def _setup_shutdown(self):
        """Setup shutdown handler using modern lifespan approach"""

        # Store reference to websocket_handler for cleanup
        if not hasattr(self.app.state, "bedrock_cleanup_handlers"):
            self.app.state.bedrock_cleanup_handlers = []

        # Add our shutdown handler to the app state
        self.app.state.bedrock_cleanup_handlers.append(self.shutdown)

        # Register atexit handler as a fallback
        atexit.register(self._sync_shutdown)

        # Try to set up lifespan handler if the app supports it and doesn't
        # have one
        try:
            if not hasattr(self.app.router, "lifespan_context") or not self.app.router.lifespan_context:

                @asynccontextmanager
                async def bedrock_lifespan(app: FastAPI):
                    """Lifespan context manager for Bedrock chat plugin"""
                    # Startup phase
                    yield
                    # Shutdown phase
                    await self.shutdown()

                self.app.router.lifespan_context = bedrock_lifespan
                logger.debug("Registered lifespan handler for Bedrock chat plugin")
        except Exception as e:
            logger.debug(f"Could not register lifespan handler, using fallback: {e}")

    async def shutdown(self):
        """Shutdown the Bedrock chat plugin"""
        try:
            await self.websocket_handler.shutdown()
            logger.info("Bedrock chat plugin shutdown complete")
        except Exception as e:
            logger.error(f"Error during shutdown: {str(e)}")

    def _sync_shutdown(self):
        """Synchronous shutdown handler for atexit"""
        try:
            # Try to get the current event loop
            try:
                loop = asyncio.get_running_loop()
                # If we get here, there's a running loop - schedule the task
                asyncio.create_task(self.shutdown())
                return
            except RuntimeError:
                # No running loop, continue to try creating one
                pass

            # Try to get or create an event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    # Loop is closed, create a new one
                    asyncio.set_event_loop(asyncio.new_event_loop())
                    loop = asyncio.get_event_loop()

                # Run the shutdown coroutine
                loop.run_until_complete(self.shutdown())
            except RuntimeError:
                # If all else fails, create and run with a new loop
                try:
                    asyncio.run(self.shutdown())
                except RuntimeError:
                    # In test environments or certain contexts, async shutdown may not be possible
                    # Just do synchronous cleanup
                    self._sync_cleanup()
        except Exception as e:
            # Suppress errors during shutdown to avoid noise in logs
            # Only log in debug mode
            logger.debug(f"Error during sync shutdown: {str(e)}")

    def _sync_cleanup(self):
        """Synchronous cleanup without async operations"""
        try:
            # Just log that we attempted cleanup - websocket handler cleanup
            # will be handled by other mechanisms or when the process exits
            logger.debug("Performing synchronous cleanup for Bedrock chat plugin")
        except Exception:
            # Silently ignore any errors during sync cleanup
            pass

    async def update_tools(self):
        """Update tools description from current FastAPI routes"""

        try:
            new_tools_desc = self.tools_generator.generate_tools_desc()
            self.config.tools_desc = new_tools_desc
            logger.info(f"Updated tools: {len(new_tools_desc.get('functions', []))} functions")
        except Exception as e:
            logger.error(f"Failed to update tools: {str(e)}")
            raise BedrockChatError(f"Tools update failed: {str(e)}")


def add_bedrock_chat(
    app: FastAPI,
    model_id: Optional[str] = None,
    aws_region: Optional[str] = None,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
    enable_ui: Optional[bool] = None,
    chat_endpoint: Optional[str] = None,
    websocket_endpoint: Optional[str] = None,
    ui_endpoint: Optional[str] = None,
    allowed_paths: Optional[list] = None,
    excluded_paths: Optional[list] = None,
    max_tool_calls: Optional[int] = None,
    timeout: Optional[int] = None,
    auth_dependency: Optional[Callable] = None,
    openapi_spec_file: Optional[str] = None,
    api_base_url: Optional[str] = None,
    **kwargs,
) -> BedrockChatPlugin:
    """
    Add Bedrock chat capabilities to a FastAPI application

    Args:
        app: FastAPI application instance
        model_id: Bedrock model ID to use
        aws_region: AWS region for Bedrock
        system_prompt: Custom system prompt
        temperature: Model temperature (0.0-1.0)
        enable_ui: Whether to enable built-in chat UI
        chat_endpoint: Base endpoint for chat API
        websocket_endpoint: WebSocket endpoint path
        ui_endpoint: Chat UI endpoint path
        allowed_paths: List of API paths to expose as tools
        excluded_paths: List of API paths to exclude from tools
        max_tool_calls: Maximum tool calls per conversation
        timeout: Timeout for API calls in seconds
        auth_dependency: Authentication dependency function
        openapi_spec_file: Path to OpenAPI spec file for framework-agnostic tool generation
        api_base_url: Base URL for API calls (e.g., http://localhost:8080)
                     RECOMMENDED: Explicitly set this for production deployments
                     instead of relying on auto-detection from environment variables
        **kwargs: Additional configuration parameters

    Returns:
        BedrockChatPlugin instance

    Raises:
        ConfigurationError: If configuration is invalid
        BedrockChatError: If plugin initialization fails

    Note:
        For production deployments, it's strongly recommended to explicitly
        configure api_base_url rather than relying on environment variable
        auto-detection, which may not work reliably in all deployment scenarios.
    """

    try:
        # Prepare configuration overrides
        config_overrides = {}

        if model_id is not None:
            config_overrides["model_id"] = model_id
        if aws_region is not None:
            config_overrides["aws_region"] = aws_region
        if system_prompt is not None:
            config_overrides["system_prompt"] = system_prompt
        if temperature is not None:
            config_overrides["temperature"] = temperature
        if enable_ui is not None:
            config_overrides["enable_ui"] = enable_ui
        if chat_endpoint is not None:
            config_overrides["chat_endpoint"] = chat_endpoint
        if websocket_endpoint is not None:
            config_overrides["websocket_endpoint"] = websocket_endpoint
        if ui_endpoint is not None:
            config_overrides["ui_endpoint"] = ui_endpoint
        if allowed_paths is not None:
            config_overrides["allowed_paths"] = allowed_paths
        if excluded_paths is not None:
            config_overrides["excluded_paths"] = excluded_paths
        if max_tool_calls is not None:
            config_overrides["max_tool_calls"] = max_tool_calls
        if timeout is not None:
            config_overrides["timeout"] = timeout
        if auth_dependency is not None:
            config_overrides["auth_dependency"] = auth_dependency
        if openapi_spec_file is not None:
            config_overrides["openapi_spec_file"] = openapi_spec_file
        if api_base_url is not None:
            config_overrides["api_base_url"] = api_base_url

        # Add any additional kwargs
        config_overrides.update(kwargs)

        # Create and return plugin
        plugin = BedrockChatPlugin(app, **config_overrides)

        return plugin

    except Exception as e:
        logger.error(f"Failed to add Bedrock chat to FastAPI app: {str(e)}")
        raise BedrockChatError(f"Plugin initialization failed: {str(e)}")


def create_tools_generator_from_spec(
    openapi_spec_file: str,
    allowed_paths: Optional[list] = None,
    excluded_paths: Optional[list] = None,
    api_base_url: Optional[str] = None,
    **config_kwargs,
) -> "ToolsGenerator":
    """
    Create a ToolsGenerator from an OpenAPI spec file for framework-agnostic usage.

    This allows using the tool generation capabilities with any framework (Express.js, Flask, etc.)
    by providing an OpenAPI spec file instead of requiring a FastAPI app.

    Args:
        openapi_spec_file: Path to OpenAPI specification file (JSON or YAML)
        allowed_paths: List of API paths to expose as tools
        excluded_paths: List of API paths to exclude from tools
        api_base_url: Base URL for API calls (auto-detected from spec if not provided)
        **config_kwargs: Additional configuration parameters for ChatConfig

    Returns:
        ToolsGenerator instance

    Example:
        ```python
        from auto_bedrock_chat_fastapi import create_tools_generator_from_spec

        # Generate tools from Express.js OpenAPI spec
        generator = create_tools_generator_from_spec(
            openapi_spec_file="./express-api-spec.json",
            allowed_paths=["/api/users", "/api/products"],
            excluded_paths=["/api/internal"],
            api_base_url="http://localhost:3000"  # Express.js server URL
        )

        # Generate tool descriptions
        tools_desc = generator.generate_tools_desc()

        # Use with any Bedrock-compatible client
        # bedrock_client.chat_completion(messages=messages, tools_desc=tools_desc)
        ```

    Raises:
        ToolsGenerationError: If spec file is invalid or not found
    """
    from .tools_generator import ToolsGenerator

    # Prepare config overrides
    config_overrides = {
        "openapi_spec_file": openapi_spec_file,
        **config_kwargs,
    }

    if allowed_paths is not None:
        config_overrides["allowed_paths"] = allowed_paths
    if excluded_paths is not None:
        config_overrides["excluded_paths"] = excluded_paths
    if api_base_url is not None:
        config_overrides["api_base_url"] = api_base_url

    # Create config
    config = load_config(**config_overrides)

    # Create and return ToolsGenerator
    return ToolsGenerator(app=None, config=config)


def create_fastapi_with_bedrock_chat(**kwargs) -> tuple[FastAPI, BedrockChatPlugin]:
    """
    Create a new FastAPI app with Bedrock chat plugin using modern lifespan handlers.

    This is the recommended way to create a new FastAPI app with Bedrock chat support.
    It properly handles startup and shutdown using the modern lifespan approach.

    Args:
        **kwargs: Configuration overrides for the ChatConfig

    Returns:
        Tuple of (FastAPI app, BedrockChatPlugin instance)

    Example:
        ```python
        from auto_bedrock_chat_fastapi import create_fastapi_with_bedrock_chat

        app, plugin = create_fastapi_with_bedrock_chat(
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            enable_ui=True
        )

        # Add your own routes
        @app.get("/")
        async def root():
            return {"message": "Hello World"}

        if __name__ == "__main__":
            import uvicorn
            uvicorn.run(app, host="0.0.0.0", port=8000)
        ```
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Lifespan context manager with Bedrock chat cleanup"""
        # Startup
        yield
        # Shutdown - cleanup Bedrock chat resources
        if hasattr(app.state, "bedrock_plugin"):
            await app.state.bedrock_plugin.shutdown()

    # Create FastAPI app with lifespan
    app = FastAPI(lifespan=lifespan)

    # Add Bedrock chat plugin
    plugin = add_bedrock_chat(app, **kwargs)

    # Store plugin reference for cleanup
    app.state.bedrock_plugin = plugin

    return app, plugin
