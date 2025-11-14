#!/usr/bin/env python3
"""
Example: Framework-Agnostic Integration with Express.js API

This example demonstrates how to use auto-bedrock-chat-fastapi with an Express.js API
by using OpenAPI specifications instead of requiring a FastAPI application.

Usage:
1. cd examples/expressjs/
2. npm install && npm start  (starts Express server on port 3000)
3. In another terminal: cd examples/expressjs && poetry run python integration.py
4. Use the generated tools with Bedrock for AI chat capabilities

Prerequisites:
- Node.js and npm installed
- Express server running (generates OpenAPI spec automatically)
- AWS credentials configured
- auto-bedrock-chat-fastapi installed
"""

import asyncio
import json
from pathlib import Path

from auto_bedrock_chat_fastapi import create_tools_generator_from_spec


def check_for_express_server_spec():
    """Check if Express server has generated an OpenAPI spec file"""
    # Look for the spec file in the current directory
    spec_file = Path("api_spec.json")

    if spec_file.exists():
        print(f"✓ Found existing OpenAPI spec from Express server: {spec_file}")
        return spec_file
    else:
        print("⚠️  No existing Express server spec found")
        print("   To get the full experience:")
        print("   1. Make sure you're in examples/expressjs/")
        print("   2. npm install")
        print("   3. npm start  (starts Express server on port 3000)")
        print("   4. The server will auto-generate api_spec.json")
        print("\n   Creating a sample spec for demo purposes...")
        return create_sample_express_openapi_spec()


def create_sample_express_openapi_spec():
    """Create a sample OpenAPI spec for demo when Express server isn't running"""
    express_api_spec = {
        "openapi": "3.0.0",
        "info": {
            "title": "Express E-commerce API (Sample)",
            "version": "1.0.0",
            "description": "Sample OpenAPI spec - start Express server for full version",
        },
        "servers": [
            {"url": "http://localhost:3000", "description": "Development server"}
        ],
        "paths": {
            "/api/v1/users": {
                "get": {
                    "summary": "Get all users",
                    "description": "Retrieve a list of all users in the system",
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "description": "Maximum number of users to return",
                            "schema": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 100,
                                "default": 10,
                            },
                        },
                        {
                            "name": "offset",
                            "in": "query",
                            "description": "Number of users to skip",
                            "schema": {"type": "integer", "minimum": 0, "default": 0},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "List of users",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "users": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "id": {"type": "integer"},
                                                        "name": {"type": "string"},
                                                        "email": {"type": "string"},
                                                    },
                                                },
                                            },
                                            "total": {"type": "integer"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
                "post": {
                    "summary": "Create a new user",
                    "description": "Create a new user account",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {
                                            "type": "string",
                                            "description": "User's full name",
                                            "minLength": 1,
                                        },
                                        "email": {
                                            "type": "string",
                                            "format": "email",
                                            "description": "User's email address",
                                        },
                                        "age": {
                                            "type": "integer",
                                            "minimum": 18,
                                            "maximum": 120,
                                            "description": "User's age",
                                        },
                                    },
                                    "required": ["name", "email"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "User created successfully",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "name": {"type": "string"},
                                            "email": {"type": "string"},
                                            "created_at": {
                                                "type": "string",
                                                "format": "date-time",
                                            },
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
            }
        },
    }

    # Save to file (sample version)
    spec_file = Path("api_spec_sample.json")
    with open(spec_file, "w") as f:
        json.dump(express_api_spec, f, indent=2)

    print(f"✓ Created sample OpenAPI spec: {spec_file}")
    return spec_file


async def demonstrate_framework_agnostic_usage():
    """Demonstrate framework-agnostic usage with Express.js OpenAPI spec"""

    print("🚀 Framework-Agnostic auto-bedrock-chat-fastapi Demo")
    print("=" * 50)

    # Step 1: Check for OpenAPI spec from Express server or create sample
    print("\n1. Looking for OpenAPI spec from Express.js server...")
    spec_file = check_for_express_server_spec()

    # Step 2: Create ToolsGenerator from OpenAPI spec
    print("\n2. Creating ToolsGenerator from OpenAPI spec...")
    try:
        generator = create_tools_generator_from_spec(
            openapi_spec_file=str(spec_file),
            allowed_paths=["/api/v1/users", "/api/v1/products", "/api/v1/orders"],
            excluded_paths=["/internal"],
            # Note: api_base_url will be auto-detected from spec servers (http://localhost:3000)
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            aws_region="us-east-1",
        )

        print("✓ ToolsGenerator created successfully")

        # Show detected API base URL
        detected_url = generator.get_api_base_url()
        print(f"✓ Detected API base URL: {detected_url}")
    except Exception as e:
        print(f"✗ Error creating ToolsGenerator: {e}")
        return

    # Step 3: Generate tool descriptions
    print("\n3. Generating tool descriptions...")
    try:
        tools_desc = generator.generate_tools_desc()
        functions = tools_desc.get("functions", [])
        print(f"✓ Generated {len(functions)} tools from Express.js API")

        print("\nGenerated Tools:")
        for func in functions:
            print(f"  📋 {func['name']}")
            print(f"      {func['description']}")

        # Show tool statistics
        stats = generator.get_tool_statistics()
        print("\n📊 Tool Statistics:")
        print(f"  - Total tools: {stats['total_tools']}")
        print(f"  - Unique paths: {stats['unique_paths']}")
        print(f"  - Methods: {stats['methods_distribution']}")

    except Exception as e:
        print(f"✗ Error generating tools: {e}")
        return

    # Step 4: Demonstrate tool validation
    print("\n4. Testing tool validation...")
    try:
        # Valid tool call
        valid_call = generator.validate_tool_call(
            "get_api_v1_users", {"limit": 10, "offset": 0}
        )
        print(f"✓ Valid tool call validation: {valid_call}")

        # Invalid tool call
        invalid_call = generator.validate_tool_call("post_api_v1_users", {})
        print(f"✓ Invalid tool call validation: {invalid_call}")

    except Exception as e:
        print(f"✗ Error in tool validation: {e}")

    # Step 5: Simulate AI conversation with tool usage
    print("\n5. Simulating AI conversation with Bedrock...")
    try:
        # Note: This would normally require AWS credentials and a running Express.js API
        print("   (Skipping actual Bedrock call - would require AWS setup)")
        print("   Example conversation flow:")
        print("   User: 'Show me all users in the system'")
        print("   AI: Would call get_api_v1_users tool")
        print("   User: 'Create a new user named John Doe'")
        print("   AI: Would call post_api_v1_users tool with user data")

        # Show what the Bedrock request would look like
        sample_messages = [
            {
                "role": "user",
                "content": "Can you show me the first 5 users in the system?",
            }
        ]

        print("\n   📄 Sample Bedrock request structure:")
        print(f"   Messages: {sample_messages}")
        print(f"   Tools available: {len(functions)} functions")
        print("   Model would have access to Express.js API endpoints")

    except Exception as e:
        print(f"✗ Error in Bedrock simulation: {e}")

    # Step 6: Show configuration options
    print("\n6. Configuration Options:")
    print("   🔧 Environment variables (.env file):")
    print("   BEDROCK_OPENAPI_SPEC_FILE=./api_spec.json")
    print(
        "   BEDROCK_API_BASE_URL=http://localhost:3000  # Auto-detected from spec if not set"
    )
    print("   BEDROCK_ALLOWED_PATHS=/api/v1/users,/api/v1/products,/api/v1/orders")
    print("   BEDROCK_EXCLUDED_PATHS=/internal,/admin")
    print("   BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0")
    print("   AWS_REGION=us-east-1")

    print("\n✅ Demo completed successfully!")
    print("\n🌐 API URL Configuration Priority:")
    print("   1. Explicit api_base_url parameter (highest priority)")
    print("   2. OpenAPI spec servers[0].url (auto-detected)")
    print("   3. Environment variable BEDROCK_API_BASE_URL")
    print("   4. Default http://localhost:8000 (fallback)")

    print("\n📚 Next Steps:")
    print("   1. Start the Express server: npm install && npm start")
    print("   2. Access Swagger UI: http://localhost:3000/api-docs")
    print("   3. Test API endpoints directly or via AI chat")
    print("   4. Set up AWS credentials for Bedrock access")
    print("   5. Configure environment variables (API URL auto-detected)")
    print("   6. Integrate with Bedrock for AI chat capabilities")

    # Cleanup:
    # If we used the temporary sample spec, remove it.
    # Otherwise (real Express server spec), retain it and inform the user.
    if spec_file.name == "api_spec_sample.json":
        if spec_file.exists():
            spec_file.unlink()
            print(f"\n🧹 Cleaned up sample spec: {spec_file}")
    else:
        print("   (Express server spec retained for reuse)")


if __name__ == "__main__":
    asyncio.run(demonstrate_framework_agnostic_usage())
