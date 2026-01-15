"""FastAPI Assistant with RAG - Crawls FastAPI docs for enhanced responses"""

from pathlib import Path

from fastapi import FastAPI

from auto_bedrock_chat_fastapi import add_bedrock_chat

# Get the directory of this file for relative paths
EXAMPLE_DIR = Path(__file__).parent

# Create FastAPI app
app = FastAPI(title="FastAPI Assistant with RAG")

# Configure Bedrock Chat with RAG enabled
bedrock_chat = add_bedrock_chat(
    app,
    # Enable RAG for FastAPI documentation knowledge
    enable_rag=True,
    # kb_sources_config=str(EXAMPLE_DIR / "kb_sources_fastapi.yaml"),
    kb_database_path=str(EXAMPLE_DIR / "fastapi_kb.db"),
    # Auto-populate on first run (development mode)
    # Production: populate manually before starting app
    # kb_populate_on_startup=True,  # Auto-populate if DB missing
    kb_allow_empty=True,  # Allow startup even if population fails
    # RAG retrieval settings
    kb_top_k_results=5,  # Retrieve top 5 relevant chunks
    kb_similarity_threshold=0.5,  # Minimum similarity score (lowered for broader matches)
    # Custom system prompt for FastAPI assistant
    system_prompt="""You are a helpful FastAPI expert assistant with access to the official FastAPI documentation.

When answering questions:
- Use the provided FastAPI documentation context to give accurate, up-to-date answers
- Include code examples when relevant
- Cite specific FastAPI features and best practices
- If the documentation doesn't cover something, clearly state that
- Always be helpful and explain concepts clearly

You have access to FastAPI tutorials, guides, and API reference documentation.""",
    # Model configuration
    model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    temperature=0.7,
    max_tokens=4096,
)

# Debug: Print actual config
print("\n🔍 DEBUG: Config after initialization")
print(f"  enable_rag: {bedrock_chat.config.enable_rag}")
print(f"  kb_database_path: {bedrock_chat.config.kb_database_path}")
print(f"  kb_top_k_results: {bedrock_chat.config.kb_top_k_results}")
print(f"  kb_similarity_threshold: {bedrock_chat.config.kb_similarity_threshold}")
print()

if __name__ == "__main__":
    import uvicorn

    print("🚀 Starting FastAPI Assistant with RAG")
    print("📚 Knowledge Base: FastAPI Documentation")
    print("🌐 Chat UI: http://localhost:8001/bedrock-chat/ui")
    print("📖 API Docs: http://localhost:8001/docs")
    print("\n⚠️  First run will auto-populate KB from FastAPI docs (2-3 minutes)")
    print("   Subsequent runs will be instant (KB cached in fastapi_kb.db)")
    uvicorn.run("app_rag:app", host="0.0.0.0", port=8001, reload=True)
