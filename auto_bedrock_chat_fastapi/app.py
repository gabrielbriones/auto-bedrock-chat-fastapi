from datetime import datetime

from auto_bedrock_chat_fastapi import add_bedrock_chat
from fastapi import FastAPI

# Create FastAPI app (existing app approach)
app = FastAPI(
    title="Auto Bedrock Chat FastAPI",
    description="Standalone Chat API with AI assistance",
    version="1.0.0",
)

# Alternative: Create app with modern lifespan (uncomment to use)
# from auto_bedrock_chat_fastapi import create_fastapi_with_bedrock_chat
# app, plugin = create_fastapi_with_bedrock_chat(
#     title="Auto Bedrock Chat FastAPI",
#     description="Standalone Chat API with AI assistance",
#     version="1.0.0",
# )


# Health check endpoint for Docker containers
@app.get("/health")
async def health_check():
    """Health check endpoint for Docker containers and load balancers"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "service": "auto-bedrock-chat-fastapi",
        "version": "1.0.0",
    }


# Add Bedrock chat capabilities using .env configuration
# All configuration comes from .env file
bedrock_chat = add_bedrock_chat(app)
