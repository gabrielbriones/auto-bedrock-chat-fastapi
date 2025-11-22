# auto-bedrock-chat-fastapi Examples

This directory contains comprehensive examples demonstrating how to integrate `auto-bedrock-chat-fastapi` with different web frameworks.

## Directory Structure

- **`expressjs/`** - Complete Express.js integration example with working server
- **`fastAPI/`** - FastAPI integration examples

## Quick Start Guide

### Express.js Integration (Recommended for Learning)

The Express.js example provides the most comprehensive demonstration of framework-agnostic capabilities.

```bash
# 1. Set up Express.js server
cd examples/expressjs/
npm install
npm start  # Starts server on port 3000

# 2. In another terminal, test Python integration
cd examples/expressjs/
poetry run python integration.py
```

### What You'll Get

1. **Complete Express.js API Server** (`expressjs/server.js`)

   - RESTful endpoints for users, products, and orders
   - Automatic OpenAPI specification generation
   - Interactive Swagger UI documentation
   - Production-ready error handling and validation

2. **Framework-Agnostic Python Integration** (`express_integration_example.py`)

   - Loads OpenAPI spec from any source (Express, FastAPI, etc.)
   - Generates AI tools automatically
   - Demonstrates tool validation and usage
   - Shows configuration options and best practices

3. **Live API Testing**
   - Test endpoints directly via curl or browser
   - View interactive documentation at http://localhost:3000/api-docs
   - Download OpenAPI spec at http://localhost:3000/api_spec.json

## Key Benefits Demonstrated

### Framework Agnostic

- Works with Express.js, FastAPI, or any framework that generates OpenAPI specs
- No framework-specific dependencies in Python code
- Consistent tool generation regardless of backend technology

### Automatic API Discovery

- Automatically detects all API endpoints from OpenAPI specification
- Generates properly typed AI tools with validation
- Handles complex request/response schemas

### Production Ready

- Environment-based configuration
- Selective endpoint exposure (exclude admin/internal APIs)
- Robust error handling and fallback mechanisms
- Clear separation between public and internal APIs

## Example API Calls

Once the Express server is running:

```bash
# Get users with pagination
curl "http://localhost:3000/api/v1/users?limit=5&offset=0"

# Create a new user
curl -X POST "http://localhost:3000/api/v1/users" \
  -H "Content-Type: application/json" \
  -d '{"name": "John Doe", "email": "john@example.com", "age": 30}'

# Get products with filtering
curl "http://localhost:3000/api/v1/products?category=Electronics&max_price=50"

# Create an order
curl -X POST "http://localhost:3000/api/v1/orders" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": 1,
    "items": [{"product_id": 1, "quantity": 2}],
    "shipping_address": {
      "street": "123 Main St",
      "city": "Anytown",
      "state": "CA",
      "zip_code": "12345"
    }
  }'
```

## AI Chat Integration

The generated tools enable natural language interactions:

```
User: "Show me the first 5 users"
AI: Calls get_api_v1_users with limit=5

User: "Create a user named Alice with email alice@test.com"
AI: Calls post_api_v1_users with the provided data

User: "What electronics products cost less than $50?"
AI: Calls get_api_v1_products with category=Electronics&max_price=50
```

## Configuration Options

```python
# Framework-agnostic configuration
generator = create_tools_generator_from_spec(
    openapi_spec_file="expressjs/express_api_spec.json",
    allowed_paths=["/api/v1/users", "/api/v1/products", "/api/v1/orders"],
    excluded_paths=["/internal", "/admin"],
    api_base_url="http://localhost:3000",  # Auto-detected from spec
    model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
    aws_region="us-east-1"
)
```

## Environment Variables

```bash
# .env file configuration
BEDROCK_OPENAPI_SPEC_FILE=./expressjs/api_spec.json
BEDROCK_API_BASE_URL=http://localhost:3000
BEDROCK_ALLOWED_PATHS=/api/v1/users,/api/v1/products,/api/v1/orders
BEDROCK_EXCLUDED_PATHS=/internal,/admin
BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0
AWS_REGION=us-east-1
```

## Next Steps

1. **Explore the Code**: Start with `expressjs/server.js` to see OpenAPI generation
2. **Test the Integration**: Run `express_integration_example.py` to see tool generation
3. **Add Your Framework**: Use the same patterns with Django, Flask, Next.js, etc.
4. **Integrate with Bedrock**: Set up AWS credentials for AI chat capabilities
5. **Production Deployment**: Configure for your production environment

## Support for Other Frameworks

The same approach works with:

- **FastAPI**: Already generates OpenAPI specs automatically
- **Django REST Framework**: With `drf-spectacular` or similar
- **Flask**: With `flask-restx` or `apispec`
- **Next.js API Routes**: With custom OpenAPI generation
- **Spring Boot**: With SpringDoc OpenAPI
- **Any framework**: That can generate/export OpenAPI 3.0 specifications

The key is having an OpenAPI specification - the Python integration is completely framework-agnostic!
