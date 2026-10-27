# Tool Calling

The plugin automatically converts your API's OpenAPI specification into AI-callable tools. The AI uses these tools to answer user questions by making real HTTP requests to your endpoints.

---

## How Tool Generation Works

`ToolsGenerator` reads your OpenAPI spec (from FastAPI's `app.openapi()` or an external file) and converts each endpoint into an AI tool description:

```
OpenAPI Operation → Tool Description
─────────────────────────────────────
operationId       → tool name
summary           → tool description
parameters        → tool input schema
requestBody       → tool input schema
responses         → tool result format
```

### Example

Given this FastAPI endpoint:

```python
@app.get(
    "/products",
    summary="List Products",
    description="Get all products, optionally filtered by category and price range."
)
async def list_products(
    category: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None
):
    ...
```

The generator creates a tool the AI can call:

```json
{
  "name": "get_products",
  "description": "Get all products, optionally filtered by category and price range.",
  "input_schema": {
    "type": "object",
    "properties": {
      "category": { "type": "string" },
      "min_price": { "type": "number" },
      "max_price": { "type": "number" }
    }
  }
}
```

---

## Tool Execution Flow

```
User: "Show me electronics under $100"
          │
          ▼
  AI decides to call tool: get_products(category="Electronics", max_price=100)
          │
          ▼
  ToolManager → HTTP GET /products?category=Electronics&max_price=100
          │
          ▼
  Your API returns: [{"id": 6, "name": "Wireless Headphones", "price": 79.99}, ...]
          │
          ▼
  AI synthesizes response: "I found 3 electronics under $100: ..."
```

Tool results go back into the model's context, so large ones count against
its input-token limit. See
[Token Management — How Oversize Is Decided](token-management.md#how-oversize-is-decided)
for how the input budget and the size of a result are estimated per model.

### Response Size Limit

Each tool response body is streamed and capped at `tool_response_max_bytes` (default 64 MiB, counted on decoded bytes,
so a compressed body is limited by its decompressed size). This protects the server's memory from a huge or endless
response; it is a safety net, not the mechanism that keeps normal large results out of the model's context.

Compressed responses are decoded in bounded steps (at most 1 MiB per decompression call, per encoding layer), so a
small high-ratio body cannot allocate far beyond the cap. Requests advertise `Accept-Encoding: gzip, deflate`, and a
response using any other `Content-Encoding` (for example `br` or `zstd`) fails with a `ToolError`.

- **At or below the cap**: the body is parsed as before (JSON, else plain text) and the result shape is unchanged.
- **Over the cap**: reading stops, the connection is closed, the body is **not** parsed, and the tool returns a flagged
  result holding the text read so far:

  ```json
  {
    "truncated": true,
    "message": "Response exceeded the 67108864-byte limit and was truncated; only the first 67108864 bytes are included.",
    "bytes_read": 67108864,
    "limit_bytes": 67108864,
    "content_type": "text/plain",
    "text": "..."
  }
  ```

  The same result is returned on the chat (graph) path, through MCP `tools/call`, and to direct LangChain
  `StructuredTool` use.

- **HTTP errors (>= 400)**: only a small bounded part of the body is read; `ToolHTTPError.details` keeps its first 500
  characters.

Raise or lower the cap with `AUTOCHAT_TOOL_RESPONSE_MAX_BYTES` (see [Configuration](configuration.md#tool-calling)).

---

## Recursive Tool Calls

The AI can make multiple sequential tool calls to solve complex queries:

```
User: "Order 2 Wireless Headphones for user john@example.com"
  → Tool: get_products(category="Electronics")        # find product ID
  → Tool: get_users(email="john@example.com")         # find user ID
  → Tool: create_order(user_id=1, items=[{...}])      # place order
  → Final answer: "Order ORD-1234 created for $159.98"
```

Configurable via:

```python
# No limit by default — set an explicit cap if needed:
add_autolangchat(app, max_tool_calls=20)
```

---

## Access Control

Control which endpoints the AI can call:

```python
add_autolangchat(
    app,
    # Only expose these paths as tools
    allowed_paths=["/products", "/users", "/orders"],
    # Always exclude these, even if in allowed_paths
    excluded_paths=["/docs", "/admin", "/internal"]
)
```

---

## Write Operations

The AI can call POST/PUT/DELETE endpoints when they are in `allowed_paths`. Add clear descriptions to your endpoints so the AI knows when to use them:

```python
@app.post(
    "/orders",
    summary="Create Order",
    description="Create a new order for a user. Requires user_id and a list of product_id + quantity pairs."
)
async def create_order(order: CreateOrder):
    ...
```

---

## Tool Authentication

When your API requires auth, pass credentials once via WebSocket. The `ToolManager` applies them automatically to all outbound tool call requests:

```python
add_autolangchat(app, enable_tool_auth=True)
```

Client sends:

```json
{ "type": "auth", "auth_type": "bearer_token", "token": "your-token" }
```

All subsequent tool calls include `Authorization: Bearer your-token`. See [Authentication](authentication.md) for all methods.

---

## Improving Tool Quality

Good endpoint descriptions lead to better AI behavior:

```python
@app.get(
    "/search",
    summary="Search Products",
    description=(
        "Full-text search across product names and categories. "
        "Use this when the user asks to find or search for products. "
        "Returns up to `limit` matching products."
    )
)
async def search_products(q: str, limit: int = 10):
    ...
```

**Tips:**

- Use `summary` for the tool name (keep it short)
- Use `description` to explain when and how to use the tool
- Use Pydantic `Field(description=...)` for parameter hints
- Include example values in descriptions

---

## See Also

- [FastAPI Plugin Integration](fastapi-plugin.md)
- [OpenAPI Integration](openapi-integration.md)
- [Authentication](authentication.md)
- [Configuration](configuration.md) — `max_tool_calls`, `max_tool_call_rounds`
- [Token Management](token-management.md) — truncation and the input-token budget for large tool results
