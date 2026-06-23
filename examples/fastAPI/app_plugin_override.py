"""Alternative example showing how to override .env settings"""

from fastapi import FastAPI

from autolangchat import add_autolangchat

# Create FastAPI app
app = FastAPI(title="Override Example")


def setup_plugin():
    """Initialize the autolangchat plugin (called only in server process, not during reload)."""
    # ====================================================================
    # EXAMPLE 1: Use all settings from .env file
    # ====================================================================
    # autolangchat_plugin = add_autolangchat(app)

    # ====================================================================
    # EXAMPLE 2: Override specific settings while keeping others from .env
    # ====================================================================
    # Uncomment the block below and comment out Example 1 to use this approach
    #
    autolangchat_plugin = add_autolangchat(
        app,
        # Override only specific settings - others come from .env
        temperature=0.3,  # Override temperature
        model_id="anthropic.claude-3-haiku-20240307-v1:0",  # Use different model
        # All other settings (system_prompt, endpoints, etc.) come from .env
    )

    # ====================================================================
    # EXAMPLE 3: Load config explicitly and modify before adding plugin
    # ====================================================================
    # Uncomment the block below and comment out Example 1 to use this approach
    #
    # config = load_config()
    # config.temperature = 0.9
    # config.max_tool_calls = 10
    # autolangchat_plugin = add_autolangchat(app, **config.model_dump())

    return autolangchat_plugin


# Initialize plugin only when running directly (not during reload imports)
if __name__ != "__main__":
    # When imported as a module (e.g., by uvicorn), initialize the plugin
    autolangchat_plugin = setup_plugin()


if __name__ == "__main__":
    import uvicorn

    # Initialize plugin for direct execution
    autolangchat_plugin = setup_plugin()

    print("🚀 Starting Override Example")
    print("📖 Documentation: http://localhost:8000/docs")
    # Pass the app object directly (not as string) to avoid re-importing the module
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
