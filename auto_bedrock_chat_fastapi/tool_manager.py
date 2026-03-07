"""Tool Manager — owns tool generation, validation, and execution.

This module encapsulates all tool-related concerns:

- **Tool generation**: ``ToolsGenerator`` converts OpenAPI specs (from FastAPI
  apps or standalone spec files) into AI-callable tool descriptions.
- **Tool management**: ``ToolManager`` creates and owns a ``ToolsGenerator``,
  caches the resulting ``tools_desc`` so it is not regenerated on every user
  message, and provides a single entry point for validation and execution.
- **Tool execution**: ``ToolManager`` makes HTTP requests to API endpoints,
  applying authentication from an ``AuthInfo`` dataclass.

``ToolManager`` is injected into ``ChatManager`` which calls it during the
tool-call loop.  ``websocket_handler.py`` no longer owns tool execution.
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import httpx
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from .auth_handler import AuthenticationHandler, Credentials
from .config import ChatConfig, load_config
from .exceptions import ToolsGenerationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ToolsGenerator
# ---------------------------------------------------------------------------


class ToolsGenerator:
    """Generates tool descriptions from FastAPI routes or OpenAPI specs for AI model consumption."""

    def __init__(
        self,
        app: Optional[FastAPI] = None,
        config: Optional[ChatConfig] = None,
        openapi_spec: Optional[Union[str, Path, Dict]] = None,
    ):
        """
        Initialize ToolsGenerator for framework-agnostic tool generation.

        Args:
            app: FastAPI application instance (for FastAPI integration)
            config: Chat configuration
            openapi_spec: OpenAPI specification - can be:
                         - Path to OpenAPI spec file (str or Path)
                         - OpenAPI spec dict directly
                         - None (will use config.openapi_spec_file or app's spec)

        Note: Either app or openapi_spec must be provided (or config.openapi_spec_file set)
        """
        self.app = app
        self.config = config or ChatConfig()
        self._openapi_schema = None
        self._generated_tools = {}
        self._openapi_spec_source = openapi_spec

        # Validate initialization parameters
        if not app and not openapi_spec and not self.config.openapi_spec_file:
            raise ToolsGenerationError(
                "Either FastAPI app, openapi_spec parameter, or " "config.openapi_spec_file must be provided"
            )

    def generate_tools_desc(self) -> Dict[str, Any]:
        """Generate tools description from FastAPI OpenAPI spec"""

        try:
            # Get OpenAPI specification
            self._openapi_schema = self._get_openapi_schema()

            tools_desc = {"type": "function", "functions": []}

            # Process each API endpoint
            for path, path_info in self._openapi_schema.get("paths", {}).items():
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
                            path=path, method=method.upper(), operation=operation
                        )

                        if function_desc:
                            tools_desc["functions"].append(function_desc)

                            # Store for later use
                            func_name = function_desc["name"]
                            self._generated_tools[func_name] = {
                                "path": path,
                                "method": method.upper(),
                                "operation": operation,
                                "function_desc": function_desc,
                            }

            logger.info(f"Generated {len(tools_desc['functions'])} tools from FastAPI routes")

            return tools_desc

        except Exception as e:
            logger.error(f"Failed to generate tools: {str(e)}")
            raise ToolsGenerationError(f"Tools generation failed: {str(e)}")

    def _get_openapi_schema(self) -> Dict[str, Any]:
        """Get OpenAPI schema from FastAPI app or OpenAPI spec file"""

        if self._openapi_schema is None:
            # Try different sources in order of priority
            if self._openapi_spec_source:
                self._openapi_schema = self._load_openapi_spec(self._openapi_spec_source)
            elif self.config.openapi_spec_file:
                self._openapi_schema = self._load_openapi_spec(self.config.openapi_spec_file)
            elif self.app:
                self._openapi_schema = get_openapi(
                    title=self.app.title or "API",
                    version=self.app.version or "1.0.0",
                    description=self.app.description or "",
                    routes=self.app.routes,
                )
            else:
                raise ToolsGenerationError(
                    "No OpenAPI spec source available. Provide either a FastAPI app, "
                    "openapi_spec parameter, or config.openapi_spec_file"
                )

        return self._openapi_schema

    def _load_openapi_spec(self, spec_source: Union[str, Path, Dict]) -> Dict[str, Any]:
        """Load OpenAPI spec from various sources"""

        # If it's already a dict, return it
        if isinstance(spec_source, dict):
            return spec_source

        # Otherwise, treat as file path
        spec_path = Path(spec_source)

        if not spec_path.exists():
            raise ToolsGenerationError(f"OpenAPI spec file not found: {spec_path}")

        try:
            with open(spec_path, "r", encoding="utf-8") as f:
                if spec_path.suffix.lower() == ".json":
                    try:
                        return json.load(f)
                    except json.JSONDecodeError as e:
                        raise ToolsGenerationError(f"Failed to parse OpenAPI spec file {spec_path} as JSON: {e}")
                else:
                    # Assume YAML format
                    try:
                        import yaml

                        try:
                            return yaml.safe_load(f)
                        except yaml.YAMLError as e:
                            raise ToolsGenerationError(f"Failed to parse OpenAPI spec file {spec_path} as YAML: {e}")
                    except ImportError:
                        raise ToolsGenerationError(
                            "YAML support requires 'pyyaml' package. " "Install with: pip install pyyaml"
                        )
        except OSError as e:
            raise ToolsGenerationError(f"Failed to open OpenAPI spec file {spec_path}: {e}")

    def get_api_base_url(self) -> str:
        """
        Determine API base URL for internal API calls.

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
            logger.debug(f"Using configured API base URL: {self.config.api_base_url}")
            return self.config.api_base_url

        # Priority 2: Extract from OpenAPI spec
        try:
            schema = self._get_openapi_schema()
            servers = schema.get("servers", [])
            if servers:
                first_server = servers[0]
                if isinstance(first_server, dict) and "url" in first_server:
                    logger.debug(f"Using API base URL from OpenAPI spec: {first_server['url']}")
                    return first_server["url"]
        except Exception as e:
            logger.debug(f"Could not extract base URL from OpenAPI spec: {e}")

        # Priority 3: Detect from environment variables
        detected_url = self._detect_runtime_base_url()
        if detected_url:
            logger.debug(f"Detected runtime API base URL: {detected_url}")
            return detected_url

        # Priority 4: Default fallback
        logger.debug("Using default API base URL: http://localhost:8000")
        return "http://localhost:8000"

    def _detect_runtime_base_url(self) -> Optional[str]:
        """
        Try to detect the base URL from runtime environment.

        Checks standard environment variables used by common deployment
        platforms and tools. For production deployments, it's recommended
        to explicitly set the api_base_url configuration parameter.
        """

        # Priority 1: Check standard environment variables
        host_env = os.getenv("HOST")
        port_env = os.getenv("PORT")
        if host_env is not None and port_env is not None:
            scheme = "https" if os.getenv("HTTPS", "").lower() in ("1", "true") else "http"
            return f"{scheme}://{host_env}:{port_env}"

        # Priority 2: Check common deployment environment variables
        for host_var, port_var in [
            ("SERVER_HOST", "SERVER_PORT"),
            ("APP_HOST", "APP_PORT"),
            ("WEB_HOST", "WEB_PORT"),
        ]:
            host = os.getenv(host_var)
            port = os.getenv(port_var)
            if host and port:
                scheme = "https" if os.getenv("HTTPS", "").lower() in ("1", "true") else "http"
                return f"{scheme}://{host}:{port}"

        return None

    def _create_function_description(self, path: str, method: str, operation: Dict) -> Optional[Dict]:
        """Create function description for Bedrock tool calling"""

        try:
            # Generate function name
            operation_id = operation.get("operationId")
            if not operation_id:
                # Generate from path and method
                clean_path = path.replace("/", "_").replace("{", "").replace("}", "")
                operation_id = f"{method.lower()}{clean_path}"

            # Clean up operation_id
            operation_id = self._clean_function_name(operation_id)

            function_desc = {
                "name": operation_id,
                "description": self._get_function_description(operation, method, path),
                "parameters": {"type": "object", "properties": {}, "required": []},
                "_metadata": {
                    "http_method": method,
                    "path": path,
                    "original_operation": operation,
                    "authentication": self._extract_auth_requirements(operation),
                },
            }

            # Extract parameters from OpenAPI spec
            self._process_parameters(function_desc, operation)
            self._process_request_body(function_desc, operation)

            return function_desc

        except Exception as e:
            logger.warning(f"Failed to create function description for {method} {path}: {str(e)}")
            return None

    def _clean_function_name(self, name: str) -> str:
        """Clean function name to be valid identifier"""

        # Replace invalid characters
        name = "".join(c if c.isalnum() or c == "_" else "_" for c in name)

        # Ensure it doesn't start with a number
        if name and name[0].isdigit():
            name = f"api_{name}"

        # Ensure it's not empty
        if not name:
            name = "api_function"

        return name

    def _get_function_description(self, operation: Dict, method: str, path: str) -> str:
        """Generate human-readable function description"""

        # Use existing description or summary
        description = operation.get("description") or operation.get("summary")

        if description:
            # Add HTTP method and path for clarity
            return f"{description.rstrip('.')}. (HTTP {method} {path})"

        # Generate description from method and path
        action_map = {
            "GET": "retrieve" if "{" in path else "list",
            "POST": "create",
            "PUT": "update",
            "PATCH": "partially update",
            "DELETE": "delete",
        }

        action = action_map.get(method, method.lower())

        # Extract resource name from path
        path_parts = [p for p in path.split("/") if p and not p.startswith("{")]
        resource = path_parts[-1] if path_parts else "resource"

        return f"{action.capitalize()} {resource} via {method} {path}"

    def _process_parameters(self, function_desc: Dict, operation: Dict):
        """Process path and query parameters"""

        parameters = operation.get("parameters", [])

        for param in parameters:
            param_name = param["name"]
            param_in = param.get("in")
            param_schema = param.get("schema", {})
            param_required = param.get("required", False)

            # Add parameter to function description
            self._add_parameter_to_function(
                function_desc=function_desc,
                param_name=param_name,
                param_schema=param_schema,
                param_description=param.get("description", f"The {param_name} parameter"),
                required=param_required,
                param_in=param_in,
            )

    def _process_request_body(self, function_desc: Dict, operation: Dict):
        """Process request body parameters"""

        request_body = operation.get("requestBody", {})
        if not request_body:
            return

        content = request_body.get("content", {})

        # Handle JSON content
        if "application/json" in content:
            json_schema = content["application/json"].get("schema", {})
            self._add_schema_to_function(function_desc, json_schema, request_body.get("required", False))

        # Handle form data
        elif "application/x-www-form-urlencoded" in content:
            form_schema = content["application/x-www-form-urlencoded"].get("schema", {})
            self._add_schema_to_function(function_desc, form_schema, request_body.get("required", False))

        # Handle multipart form data
        elif "multipart/form-data" in content:
            multipart_schema = content["multipart/form-data"].get("schema", {})
            self._add_schema_to_function(function_desc, multipart_schema, request_body.get("required", False))

    def _add_schema_to_function(self, function_desc: Dict, schema: Dict, required: bool = False):
        """Add schema properties to function parameters"""

        if schema.get("type") == "object":
            properties = schema.get("properties", {})
            schema_required = schema.get("required", [])

            for prop_name, prop_schema in properties.items():
                self._add_parameter_to_function(
                    function_desc=function_desc,
                    param_name=prop_name,
                    param_schema=prop_schema,
                    param_description=prop_schema.get("description", f"The {prop_name} field"),
                    required=prop_name in schema_required,
                    param_in="body",
                )

        elif "$ref" in schema:
            # Handle schema references
            ref_schema = self._resolve_schema_ref(schema["$ref"])
            if ref_schema:
                self._add_schema_to_function(function_desc, ref_schema, required)

    def _resolve_schema_ref(self, ref: str) -> Optional[Dict]:
        """Resolve schema reference"""

        if not ref.startswith("#/"):
            return None

        # Parse reference path
        ref_path = ref[2:].split("/")

        # Navigate through the OpenAPI schema
        current = self._openapi_schema
        for part in ref_path:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None

        return current if isinstance(current, dict) else None

    def _add_parameter_to_function(
        self,
        function_desc: Dict,
        param_name: str,
        param_schema: Dict,
        param_description: str,
        required: bool = False,
        param_in: str = "query",
    ):
        """Add parameter to function description"""

        # Convert OpenAPI schema to JSON schema
        param_def = {"description": param_description}

        # Handle different schema types
        schema_type = param_schema.get("type", "string")
        param_def["type"] = schema_type

        # Add format if present
        if "format" in param_schema:
            param_def["format"] = param_schema["format"]

        # Add enum values if present
        if "enum" in param_schema:
            param_def["enum"] = param_schema["enum"]

        # Add default value if present
        if "default" in param_schema:
            param_def["default"] = param_schema["default"]

        # Handle array types
        if schema_type == "array":
            param_def["items"] = param_schema.get("items", {"type": "string"})

        # Handle object types
        elif schema_type == "object":
            if "properties" in param_schema:
                param_def["properties"] = param_schema["properties"]
            if "required" in param_schema:
                param_def["required"] = param_schema["required"]

        # Add minimum/maximum constraints
        for constraint in [
            "minimum",
            "maximum",
            "minLength",
            "maxLength",
            "minItems",
            "maxItems",
        ]:
            if constraint in param_schema:
                param_def[constraint] = param_schema[constraint]

        # Add example if present
        if "example" in param_schema:
            param_def["example"] = param_schema["example"]

        # Add parameter to function
        function_desc["parameters"]["properties"][param_name] = param_def

        # Add to required list if necessary
        if required and param_name not in function_desc["parameters"]["required"]:
            function_desc["parameters"]["required"].append(param_name)

    def _should_exclude_path(self, path: str) -> bool:
        """Check if path should be excluded"""

        for excluded in self.config.excluded_paths:
            excluded_clean = excluded.rstrip("/")
            path_clean = path.rstrip("/")

            if path_clean.startswith(excluded_clean):
                return True

            # Handle wildcard exclusions
            if excluded_clean.endswith("*"):
                prefix = excluded_clean[:-1]
                if path_clean.startswith(prefix):
                    return True

        return False

    def _is_allowed_path(self, path: str) -> bool:
        """Check if path is in allowed list"""

        if not self.config.allowed_paths:
            return True

        for allowed in self.config.allowed_paths:
            allowed_clean = allowed.rstrip("/")
            path_clean = path.rstrip("/")

            if path_clean.startswith(allowed_clean):
                return True

            # Handle wildcard allowances
            if allowed_clean.endswith("*"):
                prefix = allowed_clean[:-1]
                if path_clean.startswith(prefix):
                    return True

        return False

    def get_tool_metadata(self, function_name: str) -> Optional[Dict]:
        """Get metadata for a specific tool"""
        return self._generated_tools.get(function_name)

    def get_all_tools_metadata(self) -> Dict[str, Dict]:
        """Get metadata for all generated tools"""
        return self._generated_tools.copy()

    def validate_tool_call(self, function_name: str, arguments: Dict[str, Any]) -> bool:
        """Validate tool call arguments against the function schema"""

        tool_metadata = self.get_tool_metadata(function_name)
        if not tool_metadata:
            return False

        function_desc = tool_metadata["function_desc"]
        parameters_schema = function_desc.get("parameters", {})

        try:
            # Check required parameters
            required_params = parameters_schema.get("required", [])
            for required_param in required_params:
                if required_param not in arguments:
                    logger.warning(f"Missing required parameter '{required_param}' for tool '{function_name}'")
                    return False

            # Validate parameter types (basic validation)
            properties = parameters_schema.get("properties", {})
            for param_name, param_value in arguments.items():
                if param_name in properties:
                    expected_type = properties[param_name].get("type")
                    if expected_type and not self._validate_parameter_type(param_value, expected_type):
                        logger.warning(f"Invalid type for parameter '{param_name}' in tool '{function_name}'")
                        return False

            return True

        except Exception as e:
            logger.error(f"Error validating tool call: {str(e)}")
            return False

    def _validate_parameter_type(self, value: Any, expected_type: str) -> bool:
        """Validate parameter type"""

        if expected_type == "string":
            return isinstance(value, str)
        elif expected_type == "integer":
            return isinstance(value, int)
        elif expected_type == "number":
            return isinstance(value, (int, float))
        elif expected_type == "boolean":
            return isinstance(value, bool)
        elif expected_type == "array":
            return isinstance(value, list)
        elif expected_type == "object":
            return isinstance(value, dict)
        else:
            # Unknown type, allow it
            return True

    def get_tool_statistics(self) -> Dict[str, Any]:
        """Get statistics about generated tools"""

        tools_count = len(self._generated_tools)
        methods_count = {}
        paths_count = len(set(tool["path"] for tool in self._generated_tools.values()))

        # Count methods
        for tool in self._generated_tools.values():
            method = tool["method"]
            methods_count[method] = methods_count.get(method, 0) + 1

        return {
            "total_tools": tools_count,
            "unique_paths": paths_count,
            "methods_distribution": methods_count,
            "excluded_paths": self.config.excluded_paths,
            "allowed_paths": self.config.allowed_paths,
            "has_restrictions": bool(self.config.allowed_paths or self.config.excluded_paths),
        }

    def _extract_auth_requirements(self, operation: Dict) -> Optional[Dict[str, Any]]:
        """
        Extract authentication requirements from OpenAPI operation.

        Supports:
        - OpenAPI 3.0 security field
        - x-auth-type custom extension (for non-standard auth types)
        - x-bearer-token-header for custom bearer header names
        - x-api-key-header for custom API key header names

        Returns:
            Dict with authentication requirements or None if none required
        """
        auth_config = {}

        # Check for OpenAPI security requirements
        security = operation.get("security")
        if security:
            # Security is a list of security schemes (OR'd together)
            auth_config["openapi_security"] = security

        # Check for custom auth type extension
        auth_type = operation.get("x-auth-type")
        if auth_type:
            auth_config["type"] = auth_type

        # Check for custom bearer header
        bearer_header = operation.get("x-bearer-token-header")
        if bearer_header:
            auth_config["bearer_token_header"] = bearer_header

        # Check for API key configuration
        api_key_header = operation.get("x-api-key-header")
        if api_key_header:
            auth_config["api_key_header"] = api_key_header

        # Check for OAuth2 token URL
        token_url = operation.get("x-oauth2-token-url")
        if token_url:
            auth_config["token_url"] = token_url

        # Check for OAuth2 scope
        scope = operation.get("x-oauth2-scope")
        if scope:
            auth_config["oauth2_scope"] = scope

        # Check for custom headers
        custom_headers = operation.get("x-custom-auth-headers")
        if custom_headers:
            auth_config["custom_headers"] = custom_headers

        # Check for basic auth requirement
        basic_auth = operation.get("x-basic-auth")
        if basic_auth:
            auth_config["basic_auth"] = basic_auth

        # Return None if no auth config found, otherwise return the config
        return auth_config if auth_config else None


# ---------------------------------------------------------------------------
# AuthInfo — lightweight, session-free authentication data
# ---------------------------------------------------------------------------


@dataclass
class AuthInfo:
    """Authentication information for tool call execution.

    This is a transport-agnostic representation of auth state.  The WebSocket
    handler (or any other caller) builds an ``AuthInfo`` from the session's
    ``Credentials`` / ``AuthenticationHandler`` before passing it to
    ``ToolManager`` or ``ChatManager``.

    Attributes:
        credentials: The ``Credentials`` dataclass (bearer token, API key, etc.).
        auth_handler: The ``AuthenticationHandler`` that knows how to apply
            ``credentials`` to HTTP request headers.
    """

    credentials: Optional[Credentials] = None
    auth_handler: Optional[AuthenticationHandler] = None

    @property
    def is_authenticated(self) -> bool:
        """Return ``True`` if usable credentials are present."""
        if self.credentials is None:
            return False
        auth_type_str = self.credentials.get_auth_type_string()
        return auth_type_str != "none"


# ---------------------------------------------------------------------------
# ToolManager
# ---------------------------------------------------------------------------


class ToolManager:
    """Manages tool generation, validation, and execution.

    ``ToolManager`` owns a ``ToolsGenerator`` (exposed as ``self.generator``)
    and handles the full lifecycle: generate tool descriptions from an OpenAPI
    spec, cache them, validate tool calls, and execute them via HTTP.

    The constructor accepts the same parameters as ``ToolsGenerator`` (``app``,
    ``openapi_spec``) so callers don't need to create one separately.  For
    testing, pass a pre-built or mock generator via ``tools_generator=``.

    Args:
        app: FastAPI application instance (creates ``ToolsGenerator`` internally).
        config: Application configuration (``ChatConfig`` instance).
        openapi_spec: OpenAPI spec (path, dict, or ``Path``) for standalone usage.
        tools_generator: Pre-built ``ToolsGenerator`` instance (overrides
            ``app``/``openapi_spec``).  Useful for testing with mocks.
        base_url: Override the auto-detected base URL.

    Example::

        # Typical usage in plugin.py — one line:
        tool_manager = ToolManager(app=app, config=config)

        # tools_desc is already cached
        print(tool_manager.tools_desc)

        # Access underlying generator
        print(tool_manager.generator.get_api_base_url())

        results = await tool_manager.execute_tool_calls(tool_calls, auth_info=auth)
    """

    def __init__(
        self,
        *,
        app: Optional[FastAPI] = None,
        config: Optional[ChatConfig] = None,
        openapi_spec: Optional[Union[str, Path, Dict]] = None,
        tools_generator: Optional[ToolsGenerator] = None,
        base_url: Optional[str] = None,
    ):
        self._config = config or ChatConfig()

        # Build or accept the generator
        if tools_generator is not None:
            self._generator = tools_generator
        else:
            self._generator = ToolsGenerator(
                app=app,
                config=self._config,
                openapi_spec=openapi_spec,
            )

        # Resolve base URL: explicit override > generator auto-detection
        if base_url is not None:
            self._base_url = base_url.rstrip("/")
        else:
            self._base_url = self._generator.get_api_base_url().rstrip("/")

        self._http_client = httpx.AsyncClient(timeout=self._config.timeout)

        # Generate and cache tools_desc once at init
        self._tools_desc: Optional[Dict[str, Any]] = self._generator.generate_tools_desc()

        # Statistics
        self._total_tool_calls_executed: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def generator(self) -> ToolsGenerator:
        """The underlying ``ToolsGenerator`` instance."""
        return self._generator

    @property
    def base_url(self) -> str:
        """The resolved API base URL."""
        return self._base_url

    @property
    def tools_desc(self) -> Optional[Dict[str, Any]]:
        """Cached tool descriptions generated at init."""
        return self._tools_desc

    def refresh_tools(self) -> None:
        """Re-generate and cache tool descriptions.

        Call this if the OpenAPI spec or route configuration changes at
        runtime (e.g. dynamic route registration).
        """
        self._tools_desc = self._generator.generate_tools_desc()
        logger.info("Tool descriptions refreshed")

    async def execute_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]],
        auth_info: Optional[AuthInfo] = None,
        on_progress: Optional[Callable] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a list of tool calls by making HTTP requests.

        Each tool call is validated, then dispatched as an HTTP request to
        the appropriate API endpoint.  Authentication headers are applied
        from ``auth_info`` when provided.

        Args:
            tool_calls: List of tool call dicts, each containing ``id``,
                ``name``, and ``arguments``.
            auth_info: Optional authentication data to apply to HTTP headers.
            on_progress: Optional async callback ``(message: str) -> None``
                invoked before each tool call for progress reporting.

        Returns:
            List of result dicts, each containing ``tool_call_id``, ``name``,
            and either ``result`` or ``error``.
        """
        results: List[Dict[str, Any]] = []
        capped_calls = tool_calls[: self._config.max_tool_calls]
        total_tools = len(capped_calls)

        for i, tool_call in enumerate(capped_calls, 1):
            function_name = tool_call.get("name")

            try:
                logger.debug(f"Executing tool call {i}/{total_tools}: {tool_call}")
                self._total_tool_calls_executed += 1

                # Optional progress callback
                if on_progress is not None:
                    await on_progress(f"Calling {function_name}... ({i}/{total_tools})")

                # Validate: tool exists
                tool_metadata = self._generator.get_tool_metadata(function_name)
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

                # Validate: arguments
                arguments = tool_call.get("arguments", {})
                if not self._generator.validate_tool_call(function_name, arguments):
                    logger.warning(f"Invalid arguments for tool {function_name}: {arguments}")
                    results.append(
                        {
                            "tool_call_id": tool_call.get("id"),
                            "name": function_name,
                            "error": "Invalid arguments",
                        }
                    )
                    continue

                # Execute
                result = await self._execute_single_tool_call(tool_metadata, arguments, auth_info)

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

    def get_statistics(self) -> Dict[str, Any]:
        """Return tool execution statistics."""
        tool_stats = self._generator.get_tool_statistics()
        return {
            "total_tool_calls_executed": self._total_tool_calls_executed,
            **tool_stats,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _execute_single_tool_call(
        self,
        tool_metadata: Dict[str, Any],
        arguments: Dict[str, Any],
        auth_info: Optional[AuthInfo] = None,
    ) -> Any:
        """Execute a single tool call via HTTP.

        Args:
            tool_metadata: Tool metadata from ``ToolsGenerator`` (path, method, etc.).
            arguments: Validated arguments for the tool call.
            auth_info: Optional authentication data for the request.

        Returns:
            The parsed response (JSON dict, or error dict on failure).
        """
        method: str = tool_metadata["method"]
        path: str = tool_metadata["path"]

        # Build URL with path parameter substitution
        url = f"{self._base_url}{path}"
        query_params: Dict[str, Any] = {}
        body_data: Dict[str, Any] = {}

        for arg_name, arg_value in arguments.items():
            if f"{{{arg_name}}}" in path:
                url = url.replace(f"{{{arg_name}}}", str(arg_value))
            elif method in ("GET", "DELETE"):
                query_params[arg_name] = arg_value
            else:
                body_data[arg_name] = arg_value

        # Prepare request kwargs
        request_kwargs: Dict[str, Any] = {
            "url": url,
            "params": query_params if query_params else None,
        }

        if method in ("POST", "PUT", "PATCH") and body_data:
            request_kwargs["json"] = body_data

        request_kwargs["headers"] = {
            "Content-Type": "application/json",
            "User-Agent": "auto-bedrock-chat-fastapi/internal",
        }

        # Apply authentication
        if auth_info and auth_info.is_authenticated and auth_info.auth_handler:
            try:
                tool_auth_config = tool_metadata.get("_metadata", {}).get("authentication")
                request_kwargs["headers"] = await auth_info.auth_handler.apply_auth_to_headers(
                    request_kwargs["headers"],
                    tool_auth_config,
                )
                auth_type_str = auth_info.credentials.get_auth_type_string()
                logger.debug(f"Applied {auth_type_str} authentication to tool call")
            except Exception as e:
                logger.error(f"Error applying authentication: {str(e)}")
                return {"error": f"Authentication failed: {str(e)}"}

        # Dispatch HTTP request
        return await self._make_http_request(method, request_kwargs)

    async def _make_http_request(
        self,
        method: str,
        request_kwargs: Dict[str, Any],
    ) -> Any:
        """Send the HTTP request and parse the response.

        Args:
            method: HTTP method (GET, POST, PUT, PATCH, DELETE).
            request_kwargs: kwargs dict for the httpx request.

        Returns:
            Parsed JSON response, or an error dict.
        """
        try:
            dispatch = {
                "GET": self._http_client.get,
                "POST": self._http_client.post,
                "PUT": self._http_client.put,
                "PATCH": self._http_client.patch,
                "DELETE": self._http_client.delete,
            }
            http_fn = dispatch.get(method)
            if http_fn is None:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response = await http_fn(**request_kwargs)

            # Error responses
            if response.status_code >= 400:
                error_detail = response.text
                try:
                    error_json = response.json()
                    error_detail = error_json.get("detail", error_detail)
                except Exception:
                    pass
                return {
                    "error": f"HTTP {response.status_code}: {error_detail}",
                    "status_code": response.status_code,
                }

            # Success
            try:
                return response.json()
            except Exception:
                return {"result": response.text, "status_code": response.status_code}

        except httpx.TimeoutException:
            return {"error": "Request timeout"}
        except httpx.RequestError as e:
            return {"error": f"Request failed: {str(e)}"}
        except Exception as e:
            return {"error": f"Unexpected error: {str(e)}"}


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------


def create_tools_generator_from_spec(
    openapi_spec_file: str,
    allowed_paths: Optional[list] = None,
    excluded_paths: Optional[list] = None,
    api_base_url: Optional[str] = None,
    **config_kwargs,
) -> ToolsGenerator:
    """Create a ToolsGenerator from an OpenAPI spec file for framework-agnostic usage.

    This allows using the tool generation capabilities with any framework
    (Express.js, Flask, etc.) by providing an OpenAPI spec file instead of
    requiring a FastAPI app.

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
