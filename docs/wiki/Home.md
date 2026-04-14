# auto-bedrock-chat-fastapi Wiki

Welcome to the **auto-bedrock-chat-fastapi** documentation wiki. This library lets you add AI chat capabilities powered by Amazon Bedrock to any FastAPI application — or any framework via an OpenAPI spec — with minimal setup.

---

## 📚 Wiki Pages

| Page                                         | Description                                                          |
| -------------------------------------------- | -------------------------------------------------------------------- |
| [Architecture](architecture)                 | System components, data flow, and module overview                    |
| [Configuration](configuration)               | All settings, `.env` reference, code overrides                       |
| [FastAPI Plugin Integration](fastapi-plugin) | How to add the plugin to a FastAPI app, with examples                |
| [OpenAPI Integration](openapi-integration)   | Framework-agnostic usage via OpenAPI specs (Express.js, Flask, etc.) |
| [Tool Calling](tool-calling)                 | How tools are generated from OpenAPI specs and called by the AI      |
| [Chat UI](chat-ui)                           | Built-in web chat interface, endpoints, and UI customization         |
| [Preset Prompts](preset-prompts)             | One-click prompt buttons, YAML format, `{{JOB_ID}}` placeholder      |
| [WebSocket Client](websocket-client)         | Python WebSocket client script, connection options, auth examples    |
| [Authentication](authentication)             | All auth methods, credential flow, and verification endpoint         |
| [SSO (Single Sign-On)](sso)                  | OAuth2/OIDC SSO integration, provider examples, and troubleshooting  |
| [RAG Feature](rag-feature)                   | Web crawler, vector DB, embedding pipeline, hybrid search            |
| [Token Management](token-management)         | Input token limits, AI summarization, text truncation                |
| [CI Pipelines](ci-pipelines)                 | GitHub Actions for tests, linting, and code quality                  |
| [CD Pipelines](cd-pipelines)                 | GitHub Actions for builds, Docker, staging, and production           |

---

## 🚀 Quick Navigation

**New to the project?** Start with [Architecture](architecture), then [FastAPI Plugin Integration](fastapi-plugin).

**Adding preset prompt buttons to the UI?** See [Preset Prompts](preset-prompts).

**Configuring the plugin?** See [Configuration](configuration).

**Adding authentication?** See [Authentication](authentication) for manual auth methods, or [SSO](sso) for Single Sign-On.

**Working with AI knowledge bases?** See [RAG Feature](rag-feature).

**Running in CI/CD?** See [CI Pipelines](ci-pipelines) and [CD Pipelines](cd-pipelines).
