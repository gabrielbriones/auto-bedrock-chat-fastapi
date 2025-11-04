"""Tools generator to convert FastAPI routes to AI-callable tools"""

import logging
import inspect
import json
from typing import Dict, List, Any, Optional, Union, Type
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from .config import ChatConfig
from .exceptions import ToolsGenerationError


logger = logging.getLogger(__name__)


class ToolsGenerator:
    """Generates tool descriptions from FastAPI routes for AI model consumption"""
    
    def __init__(self, app: FastAPI, config: ChatConfig):
        self.app = app
        self.config = config
        self._openapi_schema = None
        self._generated_tools = {}
    
    def generate_tools_desc(self) -> Dict[str, Any]:
        """Generate tools description from FastAPI OpenAPI spec"""
        
        try:
            # Get OpenAPI specification
            self._openapi_schema = self._get_openapi_schema()
            
            tools_desc = {
                "type": "function",
                "functions": []
            }
            
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
                            path=path,
                            method=method.upper(),
                            operation=operation
                        )
                        
                        if function_desc:
                            tools_desc["functions"].append(function_desc)
                            
                            # Store for later use
                            func_name = function_desc["name"]
                            self._generated_tools[func_name] = {
                                "path": path,
                                "method": method.upper(),
                                "operation": operation,
                                "function_desc": function_desc
                            }
            
            logger.info(f"Generated {len(tools_desc['functions'])} tools from FastAPI routes")
            
            return tools_desc
            
        except Exception as e:
            logger.error(f"Failed to generate tools: {str(e)}")
            raise ToolsGenerationError(f"Tools generation failed: {str(e)}")
    
    def _get_openapi_schema(self) -> Dict[str, Any]:
        """Get OpenAPI schema from FastAPI app"""
        
        if self._openapi_schema is None:
            self._openapi_schema = get_openapi(
                title=self.app.title or "API",
                version=self.app.version or "1.0.0",
                description=self.app.description or "",
                routes=self.app.routes,
            )
        
        return self._openapi_schema
    
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
            "DELETE": "delete"
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
                param_in=param_in
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
                    param_in="body"
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
        param_in: str = "query"
    ):
        """Add parameter to function description"""
        
        # Convert OpenAPI schema to JSON schema
        param_def = {
            "description": param_description
        }
        
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
        for constraint in ["minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"]:
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
            "has_restrictions": bool(self.config.allowed_paths or self.config.excluded_paths)
        }