"""Test framework-agnostic ToolsGenerator functionality"""

import json
import tempfile
from pathlib import Path

import pytest
from auto_bedrock_chat_fastapi import (
    ChatConfig,
    ToolsGenerator,
    create_tools_generator_from_spec,
    load_config,
)
from auto_bedrock_chat_fastapi.exceptions import ToolsGenerationError


class TestFrameworkAgnosticToolsGenerator:
    """Test framework-agnostic ToolsGenerator capabilities"""

    def setup_method(self):
        """Setup test environment"""
        # Sample OpenAPI spec for testing
        self.sample_openapi_spec = {
            "openapi": "3.0.0",
            "info": {"title": "Framework Agnostic Test API", "version": "1.0.0"},
            "paths": {
                "/api/users": {
                    "get": {
                        "summary": "Get all users",
                        "parameters": [
                            {
                                "name": "limit",
                                "in": "query",
                                "schema": {"type": "integer"},
                                "description": "Maximum number of users to return",
                            }
                        ],
                        "responses": {"200": {"description": "List of users"}},
                    },
                    "post": {
                        "summary": "Create a new user",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "email": {"type": "string"},
                                            "age": {"type": "integer"},
                                        },
                                        "required": ["name", "email"],
                                    }
                                }
                            },
                        },
                        "responses": {"201": {"description": "User created"}},
                    },
                },
                "/api/products/{product_id}": {
                    "get": {
                        "summary": "Get product by ID",
                        "parameters": [
                            {
                                "name": "product_id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "Product identifier",
                            }
                        ],
                        "responses": {"200": {"description": "Product details"}},
                    }
                },
                "/internal/admin": {
                    "get": {
                        "summary": "Internal admin endpoint",
                        "responses": {"200": {"description": "Admin data"}},
                    }
                },
            },
        }

    def test_tools_generator_with_dict_spec(self):
        """Test ToolsGenerator with OpenAPI spec as dict"""
        generator = ToolsGenerator(openapi_spec=self.sample_openapi_spec)
        tools_desc = generator.generate_tools_desc()

        assert "functions" in tools_desc
        functions = tools_desc["functions"]

        # Should generate tools for all endpoints
        assert len(functions) == 4

        function_names = [f["name"] for f in functions]
        assert "get_api_users" in function_names
        assert "post_api_users" in function_names
        assert "get_api_products_product_id" in function_names
        assert "get_internal_admin" in function_names

    def test_tools_generator_with_file_spec_json(self):
        """Test ToolsGenerator with OpenAPI spec from JSON file"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(self.sample_openapi_spec, f)
            spec_file = f.name

        try:
            generator = ToolsGenerator(openapi_spec=spec_file)
            tools_desc = generator.generate_tools_desc()

            assert "functions" in tools_desc
            assert len(tools_desc["functions"]) == 4
        finally:
            Path(spec_file).unlink()

    def test_tools_generator_with_file_spec_yaml(self):
        """Test ToolsGenerator with OpenAPI spec from YAML file"""
        yaml_content = """
openapi: 3.0.0
info:
  title: YAML Test API
  version: 1.0.0
paths:
  /yaml/test:
    get:
      summary: YAML test endpoint
      responses:
        200:
          description: Success
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            spec_file = f.name

        try:
            generator = ToolsGenerator(openapi_spec=spec_file)
            tools_desc = generator.generate_tools_desc()

            assert "functions" in tools_desc
            assert len(tools_desc["functions"]) == 1
            assert tools_desc["functions"][0]["name"] == "get_yaml_test"
        finally:
            Path(spec_file).unlink()

    def test_tools_generator_with_config_file_path(self):
        """Test ToolsGenerator using config with openapi_spec_file"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(self.sample_openapi_spec, f)
            spec_file = f.name

        try:
            config = load_config(openapi_spec_file=spec_file)
            generator = ToolsGenerator(app=None, config=config)
            tools_desc = generator.generate_tools_desc()

            assert "functions" in tools_desc
            assert len(tools_desc["functions"]) == 4
        finally:
            Path(spec_file).unlink()

    def test_create_tools_generator_from_spec_function(self):
        """Test create_tools_generator_from_spec convenience function"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(self.sample_openapi_spec, f)
            spec_file = f.name

        try:
            generator = create_tools_generator_from_spec(
                openapi_spec_file=spec_file,
                allowed_paths=["/api/users"],
                excluded_paths=["/internal"],
            )

            tools_desc = generator.generate_tools_desc()
            function_names = [f["name"] for f in tools_desc["functions"]]

            # Should only include allowed paths and exclude internal
            assert "get_api_users" in function_names
            assert "post_api_users" in function_names
            assert "get_internal_admin" not in function_names
        finally:
            Path(spec_file).unlink()

    def test_path_filtering_with_framework_agnostic(self):
        """Test path filtering works with framework-agnostic mode"""
        from auto_bedrock_chat_fastapi import load_config

        # Use load_config to properly set path filtering
        config = load_config(
            allowed_paths=["/api/users"],
            excluded_paths=["/internal"],
        )

        generator = ToolsGenerator(openapi_spec=self.sample_openapi_spec, config=config)
        tools_desc = generator.generate_tools_desc()

        function_names = [f["name"] for f in tools_desc["functions"]]

        # Should include allowed paths
        assert "get_api_users" in function_names
        assert "post_api_users" in function_names

        # Should exclude internal paths (note: /internal/admin starts with /internal)
        assert "get_internal_admin" not in function_names

        # Should exclude non-allowed paths
        assert "get_api_products_product_id" not in function_names

    def test_error_handling_missing_file(self):
        """Test error handling for missing OpenAPI spec file"""
        with pytest.raises(ToolsGenerationError, match="OpenAPI spec file not found"):
            generator = ToolsGenerator(openapi_spec="/nonexistent/file.json")
            generator.generate_tools_desc()

    def test_error_handling_invalid_json(self):
        """Test error handling for invalid JSON file"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("invalid json content")
            spec_file = f.name

        try:
            with pytest.raises(
                ToolsGenerationError, match="Failed to parse OpenAPI spec"
            ):
                generator = ToolsGenerator(openapi_spec=spec_file)
                generator.generate_tools_desc()
        finally:
            Path(spec_file).unlink()

    def test_error_handling_no_sources(self):
        """Test error handling when no OpenAPI sources are provided"""
        with pytest.raises(
            ToolsGenerationError, match="Either FastAPI app, openapi_spec parameter"
        ):
            ToolsGenerator()

        # Test error when trying to generate tools without proper setup
        with pytest.raises(ToolsGenerationError):
            config = ChatConfig()  # No openapi_spec_file set
            generator = ToolsGenerator(app=None, config=config)
            generator.generate_tools_desc()

    def test_tool_metadata_with_framework_agnostic(self):
        """Test tool metadata functionality with framework-agnostic mode"""
        generator = ToolsGenerator(openapi_spec=self.sample_openapi_spec)
        generator.generate_tools_desc()  # Generate tools to populate metadata

        # Test individual tool metadata
        metadata = generator.get_tool_metadata("get_api_users")
        assert metadata is not None
        assert metadata["method"] == "GET"
        assert metadata["path"] == "/api/users"

        # Test all tools metadata
        all_metadata = generator.get_all_tools_metadata()
        assert len(all_metadata) == 4

        # Test tool statistics
        stats = generator.get_tool_statistics()
        assert stats["total_tools"] == 4
        assert stats["unique_paths"] == 3

    def test_tool_validation_with_framework_agnostic(self):
        """Test tool call validation with framework-agnostic mode"""
        generator = ToolsGenerator(openapi_spec=self.sample_openapi_spec)
        generator.generate_tools_desc()  # Generate tools to enable validation

        # Valid tool call
        assert generator.validate_tool_call("get_api_users", {"limit": 10})

        # Invalid tool call (missing required parameter for POST)
        assert not generator.validate_tool_call("post_api_users", {})

        # Valid tool call with required parameters
        assert generator.validate_tool_call(
            "post_api_users", {"name": "John", "email": "john@example.com"}
        )

    def test_parameter_processing_with_framework_agnostic(self):
        """Test parameter processing works correctly with framework-agnostic mode"""
        generator = ToolsGenerator(openapi_spec=self.sample_openapi_spec)
        tools_desc = generator.generate_tools_desc()

        # Find the GET users function
        get_users_func = next(
            f for f in tools_desc["functions"] if f["name"] == "get_api_users"
        )

        # Should have query parameter
        properties = get_users_func["parameters"]["properties"]
        assert "limit" in properties
        assert properties["limit"]["type"] == "integer"

        # Find the POST users function
        post_users_func = next(
            f for f in tools_desc["functions"] if f["name"] == "post_api_users"
        )

        # Should have body parameters
        properties = post_users_func["parameters"]["properties"]
        assert "name" in properties
        assert "email" in properties
        assert "age" in properties

        # Should have required parameters
        required = post_users_func["parameters"]["required"]
        assert "name" in required
        assert "email" in required
        assert "age" not in required

    def test_priority_order_for_spec_sources(self):
        """Test that spec sources are used in correct priority order"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(self.sample_openapi_spec, f)
            spec_file = f.name

        try:
            # Priority 1: Direct openapi_spec parameter should take precedence
            different_spec = {
                "openapi": "3.0.0",
                "info": {"title": "Priority Test", "version": "1.0.0"},
                "paths": {
                    "/priority/test": {
                        "get": {
                            "summary": "Priority endpoint",
                            "responses": {"200": {"description": "Success"}},
                        }
                    }
                },
            }

            config = ChatConfig(openapi_spec_file=spec_file)
            generator = ToolsGenerator(openapi_spec=different_spec, config=config)
            tools_desc = generator.generate_tools_desc()

            # Should use the direct spec, not the config file
            function_names = [f["name"] for f in tools_desc["functions"]]
            assert "get_priority_test" in function_names
            assert len(tools_desc["functions"]) == 1

        finally:
            Path(spec_file).unlink()

    def test_api_base_url_extraction(self):
        """Test API base URL extraction from OpenAPI specs and configuration"""

        # Test 1: Extract URL from OpenAPI servers
        spec_with_servers = {
            "openapi": "3.0.0",
            "info": {"title": "Server Test API", "version": "1.0.0"},
            "servers": [
                {"url": "http://localhost:3000", "description": "Express.js server"},
                {"url": "https://api.production.com", "description": "Production"},
            ],
            "paths": {
                "/test": {"get": {"responses": {"200": {"description": "Success"}}}}
            },
        }

        generator = ToolsGenerator(openapi_spec=spec_with_servers)
        base_url = generator.get_api_base_url()
        assert base_url == "http://localhost:3000"

        # Test 2: Configuration override should take priority
        config = load_config(api_base_url="http://localhost:8080")
        generator = ToolsGenerator(openapi_spec=spec_with_servers, config=config)
        base_url = generator.get_api_base_url()
        assert base_url == "http://localhost:8080"

        # Test 3: Spec without servers should use default
        spec_no_servers = {
            "openapi": "3.0.0",
            "info": {"title": "No Servers API", "version": "1.0.0"},
            "paths": {
                "/test": {"get": {"responses": {"200": {"description": "Success"}}}}
            },
        }

        generator = ToolsGenerator(openapi_spec=spec_no_servers)
        base_url = generator.get_api_base_url()
        assert base_url == "http://localhost:8000"

        # Test 4: HTTPS and custom ports
        spec_https = {
            "openapi": "3.0.0",
            "info": {"title": "HTTPS API", "version": "1.0.0"},
            "servers": [{"url": "https://secure-api.example.com:8443"}],
            "paths": {
                "/secure": {"get": {"responses": {"200": {"description": "Success"}}}}
            },
        }

        generator = ToolsGenerator(openapi_spec=spec_https)
        base_url = generator.get_api_base_url()
        assert base_url == "https://secure-api.example.com:8443"

    def test_create_tools_generator_from_spec_with_api_url(self):
        """Test create_tools_generator_from_spec with api_base_url parameter"""

        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Custom URL Test", "version": "1.0.0"},
            "servers": [{"url": "http://localhost:3000"}],  # Should be overridden
            "paths": {
                "/api/test": {"get": {"responses": {"200": {"description": "Success"}}}}
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(spec, f)
            spec_file = f.name

        try:
            # Custom API base URL should override spec servers
            generator = create_tools_generator_from_spec(
                openapi_spec_file=spec_file, api_base_url="http://my-custom-api:9000"
            )

            base_url = generator.get_api_base_url()
            assert base_url == "http://my-custom-api:9000"

            # Tools should still be generated correctly
            tools_desc = generator.generate_tools_desc()
            assert len(tools_desc["functions"]) == 1
            assert tools_desc["functions"][0]["name"] == "get_api_test"

        finally:
            Path(spec_file).unlink()
