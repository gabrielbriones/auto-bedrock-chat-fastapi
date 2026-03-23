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
add_bedrock_chat(app, max_tool_call_rounds=10, max_tool_calls=10)
```

---

## Access Control

Control which endpoints the AI can call:

```python
add_bedrock_chat(
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
add_bedrock_chat(app, enable_tool_auth=True)
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
