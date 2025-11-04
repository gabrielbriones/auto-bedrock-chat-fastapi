"""Alternative example showing how to override .env settings"""

from fastapi import FastAPI
from auto_bedrock_chat_fastapi import add_bedrock_chat

# Create FastAPI app
app = FastAPI(title="Override Example")

# Example 1: Use all settings from .env file
bedrock_chat_default = add_bedrock_chat(app)

# Example 2: Override specific settings while keeping others from .env
bedrock_chat_override = add_bedrock_chat(
    app,
    # Override only specific settings - others come from .env
    temperature=0.3,  # Override temperature
    enable_ui=False,  # Disable UI
    model_id="anthropic.claude-3-haiku-20240307-v1:0",  # Use different model
    # All other settings (system_prompt, endpoints, etc.) come from .env
)

# Example 3: Load config explicitly and modify
from auto_bedrock_chat_fastapi.config import load_config

# Load base config from .env
config = load_config()

# Modify specific settings
config.temperature = 0.9
config.max_tool_calls = 10

# Use the modified config
bedrock_chat_custom = add_bedrock_chat(app, **config.dict())

if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting Override Example")
    print("📖 Documentation: http://localhost:8001/docs")
    uvicorn.run("example_override:app", host="0.0.0.0", port=8001, reload=True)