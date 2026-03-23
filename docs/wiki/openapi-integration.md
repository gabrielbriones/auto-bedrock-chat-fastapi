# OpenAPI Spec Integration

`auto-bedrock-chat-fastapi` works with **any framework** — not just FastAPI. By providing an OpenAPI 3.x specification file, the plugin can generate AI tools and forward calls to any HTTP server (Express.js, Flask, Django, Spring Boot, etc.).

---

## How It Works

1. You provide a path to an OpenAPI spec JSON/YAML file via `openapi_spec_file`.
2. `ToolsGenerator` parses the spec and creates AI-callable tool descriptions for every endpoint.
3. When the AI triggers a tool call, `ToolManager` makes an HTTP request to the `servers[0].url` defined in the spec (auto-detected) or a base URL you configure.

---

## FastAPI: Use Your Own OpenAPI Spec

By default the plugin reads from `app.openapi()`. To use an external spec instead:

```python
bedrock_chat = add_bedrock_chat(
    app,
    openapi_spec_file="path/to/api_spec.json"
)
```

---

## Express.js Integration

### 1. Generate the OpenAPI Spec from Express.js

Install `swagger-jsdoc` and add JSDoc annotations:

```bash
npm install swagger-jsdoc swagger-ui-express
```

```javascript
// server.js
const swaggerJsdoc = require("swagger-jsdoc");
const fs = require("fs");

const options = {
  definition: {
    openapi: "3.0.0",
    info: { title: "My Express API", version: "1.0.0" },
    servers: [{ url: "http://localhost:3000" }],
  },
  apis: ["./routes/*.js"],
};

const spec = swaggerJsdoc(options);
fs.writeFileSync("api_spec.json", JSON.stringify(spec, null, 2));
```

Or use the helper script:

```bash
node examples/expressjs/generate-spec.js
```

### 2. Start the Python Plugin Pointing at the Spec

```python
# standalone_chat.py
from fastapi import FastAPI
from auto_bedrock_chat_fastapi import add_bedrock_chat

app = FastAPI(title="Chat Proxy")

bedrock_chat = add_bedrock_chat(
    app,
    openapi_spec_file="examples/expressjs/api_spec.json",
    system_prompt="You are a helpful assistant for our e-commerce store.",
    enable_ui=True
)
```

The plugin reads `servers[0].url` from the spec (`http://localhost:3000`) and forwards all tool calls there.

### 3. Run Both Servers

```bash
# Terminal 1: Express.js API
cd examples/expressjs && node server.js

# Terminal 2: Python chat proxy
uvicorn standalone_chat:app --port 8001
```

---

## Flask / Django / Other Frameworks

The same pattern applies for any framework with OpenAPI support:

```python
# Generate spec with your framework, then:
bedrock_chat = add_bedrock_chat(
    app,
    openapi_spec_file="my_flask_api_spec.json"
)
```

Popular spec generators:

- Flask: `flask-restx`, `flasgger`
- Django: `drf-spectacular`
- Spring Boot: `springdoc-openapi`

---

## Manual ToolsGenerator Usage

For advanced scenarios, use `ToolsGenerator` directly:

```python
from auto_bedrock_chat_fastapi.tool_manager import ToolsGenerator

# From a spec file
generator = ToolsGenerator(openapi_spec="path/to/spec.json")

# From a dict
with open("spec.json") as f:
    spec = json.load(f)
generator = ToolsGenerator(openapi_spec=spec)

tools_desc = generator.generate_tools_desc()
```

---

## Supported Spec Formats

| Format           | Support                          |
| ---------------- | -------------------------------- |
| OpenAPI 3.0 JSON | ✅                               |
| OpenAPI 3.1 JSON | ✅                               |
| OpenAPI 3.0 YAML | ✅                               |
| Swagger 2.0      | ⚠️ Partial (path/method parsing) |

---

## See Also

- [FastAPI Plugin Integration](fastapi-plugin.md)
- [Tool Calling](tool-calling.md) — how the generated tools work
- `examples/expressjs/` — full Express.js integration demo
