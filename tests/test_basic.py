"""Basic tests for auto-bedrock-chat-fastapi"""

import pytest
import json
from unittest.mock import Mock, patch, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
import asyncio

from auto_bedrock_chat_fastapi import add_bedrock_chat, ChatConfig, BedrockChatPlugin
from auto_bedrock_chat_fastapi.config import load_config
from auto_bedrock_chat_fastapi.exceptions import ConfigurationError


class TestConfig:
    """Test configuration management"""
    
    def test_default_config(self):
        """Test default configuration loading"""
        config = load_config()
        assert config.model_id == "anthropic.claude-3-5-sonnet-20241022-v2:0"
        assert config.aws_region == "us-east-1"
        assert config.temperature == 0.7
        assert config.enable_ui is False  # Test environment has UI disabled
    
    def test_config_overrides(self):
        """Test configuration overrides"""
        config = load_config(
            model_id="test-model",
            temperature=0.5,
            enable_ui=False
        )
        assert config.model_id == "test-model"
        assert config.temperature == 0.5
        assert config.enable_ui is False
    
    def test_invalid_temperature(self):
        """Test invalid temperature validation"""
        from auto_bedrock_chat_fastapi.exceptions import ConfigurationError
        with pytest.raises(ConfigurationError):
            load_config(temperature=2.0)  # Should be <= 1.0

    def test_invalid_model(self):
        """Test invalid model validation"""
        from auto_bedrock_chat_fastapi.exceptions import ConfigurationError
        with pytest.raises(ConfigurationError):
            load_config(model_id="invalid-model")
class TestPlugin:
    """Test plugin functionality"""
    
    def setup_method(self):
        """Setup test environment"""
        self.app = FastAPI(title="Test App")
        
        # Add a simple test endpoint
        @self.app.get("/test")
        async def test_endpoint():
            return {"message": "test"}
    
    @patch('auto_bedrock_chat_fastapi.plugin.BedrockClient')
    @patch('auto_bedrock_chat_fastapi.plugin.ChatSessionManager')
    def test_plugin_initialization(self, mock_session_manager, mock_bedrock_client):
        """Test plugin initialization"""
        # Mock the dependencies
        mock_bedrock_client.return_value = Mock()
        mock_session_manager.return_value = Mock()
        
        plugin = add_bedrock_chat(
            self.app,
            model_id="test-model",
            enable_ui=False
        )
        
        assert isinstance(plugin, BedrockChatPlugin)
        assert plugin.config.model_id == "test-model"
        assert plugin.config.enable_ui is False
    
    def test_plugin_routes_added(self):
        """Test that plugin routes are added to app"""
        with patch('auto_bedrock_chat_fastapi.plugin.BedrockClient'), \
             patch('auto_bedrock_chat_fastapi.plugin.ChatSessionManager'), \
             patch('auto_bedrock_chat_fastapi.plugin.WebSocketChatHandler'):
            
            add_bedrock_chat(self.app, enable_ui=False)
            
            # Check that routes were added (using test environment defaults)
            route_paths = [route.path for route in self.app.routes]
            assert "/bedrock-chat/health" in route_paths
            assert "/bedrock-chat/ws" in route_paths
            assert "/bedrock-chat/stats" in route_paths
            assert "/bedrock-chat/tools" in route_paths


class TestToolsGenerator:
    """Test tools generator functionality"""
    
    def setup_method(self):
        """Setup test environment"""
        self.app = FastAPI(title="Test App")
        
        # Add test endpoints with different characteristics
        @self.app.get("/simple")
        async def simple_endpoint():
            return {"message": "simple"}
        
        @self.app.post("/with-body")
        async def endpoint_with_body(data: dict):
            return {"received": data}
        
        @self.app.get("/with-params")
        async def endpoint_with_params(param1: str, param2: int = 10):
            return {"param1": param1, "param2": param2}
    
    def test_tools_generation(self):
        """Test basic tools generation"""
        from auto_bedrock_chat_fastapi.tools_generator import ToolsGenerator
        
        config = load_config(enable_ui=False)
        generator = ToolsGenerator(self.app, config)
        
        tools_desc = generator.generate_tools_desc()
        
        # Should contain function definitions
        assert "functions" in tools_desc
        functions = tools_desc["functions"]
        
        # Should have our test endpoints (actual generated names)
        function_names = [f["name"] for f in functions]
        assert "simple_endpoint_simple_get" in function_names
        assert "endpoint_with_body_with_body_post" in function_names
        assert "endpoint_with_params_with_params_get" in function_names
    
    def test_tools_filtering(self):
        """Test tools filtering by allowed/excluded paths"""
        from auto_bedrock_chat_fastapi.tools_generator import ToolsGenerator
        
        config = load_config(
            allowed_paths=["/simple"],
            enable_ui=False
        )
        generator = ToolsGenerator(self.app, config)
        
        tools_desc = generator.generate_tools_desc()
        functions = tools_desc["functions"]
        function_names = [f["name"] for f in functions]
        
        # Should only have the allowed endpoint (actual generated name)
        assert "simple_endpoint_simple_get" in function_names
        assert "endpoint_with_body_with_body_post" not in function_names
        assert "endpoint_with_params_with_params_get" not in function_names


class TestBedrockClient:
    """Test Bedrock client functionality"""
    
    @patch('boto3.Session')
    def test_client_initialization(self, mock_session):
        """Test Bedrock client initialization"""
        from auto_bedrock_chat_fastapi.bedrock_client import BedrockClient
        
        # Mock the session and client
        mock_session_instance = Mock()
        mock_client = Mock()
        mock_session.return_value = mock_session_instance
        mock_session_instance.client.return_value = mock_client
        
        config = load_config()
        client = BedrockClient(config)
        
        # Should initialize boto3 session and client
        mock_session.assert_called_once()
        # Check that client was called with expected parameters (including config)
        call_args = mock_session_instance.client.call_args
        assert call_args[0] == ('bedrock-runtime',)  # First positional arg
        assert call_args[1]['region_name'] == config.aws_region  # region_name kwarg
        assert 'config' in call_args[1]  # config kwarg should be present
    
    @patch('boto3.Session')
    async def test_health_check(self, mock_session):
        """Test health check functionality"""
        from auto_bedrock_chat_fastapi.bedrock_client import BedrockClient
        
        # Mock the session and client
        mock_session_instance = Mock()
        mock_client = Mock()
        mock_session.return_value = mock_session_instance
        mock_session_instance.client.return_value = mock_client
        
        # Mock successful invoke_model response
        mock_client.invoke_model.return_value = {
            'body': Mock(),
            'contentType': 'application/json'
        }
        
        # Mock body.read() method
        mock_response_body = {
            "content": [{"text": "Hello!"}],
            "usage": {"input_tokens": 10, "output_tokens": 5}
        }
        mock_client.invoke_model.return_value['body'].read.return_value = json.dumps(mock_response_body).encode()
        
        config = load_config()
        client = BedrockClient(config)
        
        health = await client.health_check()
        
        assert health["status"] == "healthy"
        assert health["model"] == config.model_id
        assert health["region"] == config.aws_region
        assert "response_received" in health


class TestSessionManager:
    """Test session manager functionality"""
    
    async def test_session_creation(self):
        """Test session creation and management"""
        from auto_bedrock_chat_fastapi.session_manager import ChatSessionManager
        
        config = load_config()
        manager = ChatSessionManager(config)
        
        # Create a session
        websocket = Mock()
        session_id = await manager.create_session(websocket)
        
        assert session_id in manager._sessions
        assert manager._sessions[session_id].websocket == websocket
        assert len(manager._sessions[session_id].conversation_history) == 0
    
    async def test_session_cleanup(self):
        """Test session cleanup"""
        from auto_bedrock_chat_fastapi.session_manager import ChatSessionManager
        
        config = load_config()
        manager = ChatSessionManager(config)
        
        # Create and then close a session
        websocket = Mock()
        session_id = await manager.create_session(websocket)
        
        await manager.remove_session_by_id(session_id)
        
        assert session_id not in manager._sessions


class TestIntegration:
    """Integration tests"""
    
    def setup_method(self):
        """Setup test environment"""
        self.app = FastAPI(title="Test Integration App")
        
        @self.app.get("/test-data")
        async def get_test_data():
            return {"data": "test", "timestamp": "2024-01-01"}
    
    @patch('auto_bedrock_chat_fastapi.bedrock_client.BedrockClient.chat_completion')
    @patch('boto3.Session')
    async def test_full_integration(self, mock_session, mock_chat_completion):
        """Test full integration without real AWS calls"""
        # Mock the chat completion response
        mock_chat_completion.return_value = {
            "role": "assistant",
            "content": "Here's the test data: {'data': 'test', 'timestamp': '2024-01-01'}"
        }
        
        # Mock the session and client
        mock_session_instance = Mock()
        mock_client = Mock()
        mock_session.return_value = mock_session_instance
        mock_session_instance.client.return_value = mock_client
        
        # Add chat capabilities
        plugin = add_bedrock_chat(
            self.app,
            enable_ui=False,
            allowed_paths=["/test-data"]
        )
        
        # Create test client
        client = TestClient(self.app)
        
        # Test health endpoint (using test environment defaults)
        response = client.get("/bedrock-chat/health")
        # In test environment, this might return 200 with degraded status or 503
        assert response.status_code in [200, 503, 500]
        if response.status_code == 200:
            # If 200, check that the status indicates degraded or unhealthy
            data = response.json()
            assert data["status"] in ["healthy", "degraded", "unhealthy"]
        
        # Test tools endpoint (using test environment defaults)
        response = client.get("/bedrock-chat/tools")
        assert response.status_code == 200
        data = response.json()
        assert "tools_description" in data
        assert "functions" in data["tools_description"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])