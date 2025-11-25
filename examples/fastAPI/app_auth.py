"""Example FastAPI application with multiple authentication methods and auto-bedrock-chat-fastapi

This example demonstrates:
1. Bearer Token authentication (JWT-style tokens)
2. HTTP Basic Authentication (username:password)
3. API Key authentication (custom header-based)
4. OAuth2 Client Credentials flow (token exchange)
5. Protected order endpoints (orders only accessible to logged-in users)
6. User isolation (users can only see/create their own orders)
7. Integration with auto-bedrock-chat-fastapi authentication system
8. Public product endpoints and protected order endpoints

The authentication system supports multiple methods that can be tested via the UI.
For production, use proper JWT libraries like python-jose or PyJWT.
"""

import random
import string
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import secrets
import base64

from fastapi import FastAPI, HTTPException, Depends, Body, Request, Form
from fastapi.openapi.utils import get_openapi
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

# Import the plugin
from auto_bedrock_chat_fastapi import add_bedrock_chat

# Create FastAPI app
app = FastAPI(
    title="Example E-commerce API with Authentication",
    description="E-commerce API with AI chat assistance and token-based authentication",
    version="1.0.0",
)

# Configure security scheme for Swagger UI
security = HTTPBearer(description="Bearer token authentication", auto_error=True)
basic_security = HTTPBasic(description="Basic authentication with username and password")

# ============================================================================
# Authentication Models and Storage
# ============================================================================

class LoginRequest(BaseModel):
    """User login credentials"""
    username: str = Field(..., description="Username")
    password: str = Field(..., description="Password")


class LoginResponse(BaseModel):
    """Login response with access token"""
    access_token: str = Field(..., description="Bearer token for authentication")
    token_type: str = Field(default="bearer", description="Token type")
    user_id: int = Field(..., description="Authenticated user ID")
    username: str = Field(..., description="Username")
    expires_in: int = Field(..., description="Token expiration time in seconds")


class CurrentUser(BaseModel):
    """Current authenticated user information"""
    id: int = Field(..., description="User ID")
    username: str = Field(..., description="Username")
    email: str = Field(..., description="Email address")


# Simple in-memory token storage (in production use proper JWT)
# Format: {token: {"user_id": int, "expires_at": datetime}}
active_tokens: Dict[str, Dict[str, Any]] = {}

# API Key storage (in production use database)
# Format: {"api_key": {"user_id": int, "name": str, "active": bool}}
api_keys: Dict[str, Dict[str, Any]] = {
    "sk_test_alice_12345": {
        "user_id": 1,
        "name": "Alice's API Key",
        "active": True,
    },
    "sk_test_bob_67890": {
        "user_id": 2,
        "name": "Bob's API Key",
        "active": True,
    },
}

# OAuth2 Client storage (for OAuth2 Client Credentials flow)
# Format: {"client_id": {"client_secret": str, "user_id": int, "scopes": list}}
oauth2_clients: Dict[str, Dict[str, Any]] = {
    "client_alice": {
        "client_secret": "secret_alice_12345",
        "user_id": 1,
        "scopes": ["read:orders", "write:orders"],
    },
    "client_bob": {
        "client_secret": "secret_bob_67890",
        "user_id": 2,
        "scopes": ["read:orders"],
    },
}

# User credentials storage (in production use proper password hashing)
# Format: {"username": {"password": str, "user_id": int, "email": str}}
user_credentials: Dict[str, Dict[str, Any]] = {
    "alice": {
        "password": "password123",
        "user_id": 1,
        "email": "alice@example.com",
    },
    "bob": {
        "password": "password456",
        "user_id": 2,
        "email": "bob@example.com",
    },
    "charlie": {
        "password": "password789",
        "user_id": 3,
        "email": "charlie@example.com",
    },
}

# ============================================================================
# Authentication Functions
# ============================================================================

def get_current_user(
    bearer: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False)),
    basic: HTTPBasicCredentials = Depends(HTTPBasic(auto_error=False)),
    request: Request = None,
) -> CurrentUser:
    """
    Dependency to extract and validate the current user from:
    - Bearer token in Authorization header (JWT or OAuth2 token)
    - Basic auth (username:password)
    - API Key in X-API-Key header
    - Bearer token (OAuth2 access token from /oauth2/token)
    
    Usage:
        @app.get("/protected")
        async def protected_route(user: CurrentUser = Depends(get_current_user)):
            return {"message": f"Hello {user.username}"}
    """
    # Try bearer token first (both JWT tokens and OAuth2 access tokens)
    if bearer:
        token = bearer.credentials
        
        # Check if it's a JWT-style token
        if token in active_tokens:
            token_data = active_tokens[token]
            
            # Check expiration
            if datetime.now(timezone.utc) > token_data["expires_at"]:
                del active_tokens[token]
                raise HTTPException(status_code=401, detail="Token expired")
            
            # Return current user information
            user_id = token_data["user_id"]
            return CurrentUser(
                id=user_id,
                username=token_data["username"],
                email=token_data["email"],
            )
        # If not found in active_tokens, it's invalid
        else:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    # Try basic auth
    elif basic:
        username = basic.username
        password = basic.password
        
        # Verify credentials
        if username not in user_credentials:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        
        user_creds = user_credentials[username]
        
        if user_creds["password"] != password:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        
        # Return current user information
        user_id = user_creds["user_id"]
        email = user_creds["email"]
        return CurrentUser(
            id=user_id,
            username=username,
            email=email,
        )
    
    # Try API Key from header
    api_key = None
    if request:
        api_key = request.headers.get("X-API-Key")
    
    if api_key:
        if api_key not in api_keys:
            raise HTTPException(status_code=401, detail="Invalid API key")
        
        api_key_data = api_keys[api_key]
        if not api_key_data.get("active"):
            raise HTTPException(status_code=401, detail="API key is inactive")
        
        user_id = api_key_data["user_id"]
        # Get user info from user_credentials
        for username, creds in user_credentials.items():
            if creds["user_id"] == user_id:
                return CurrentUser(
                    id=user_id,
                    username=username,
                    email=creds["email"],
                )
        raise HTTPException(status_code=401, detail="User not found")
    
    # No credentials provided
    raise HTTPException(status_code=401, detail="Authorization required")


# ============================================================================
# Mock Database
# ============================================================================

products_db: Dict[int, Dict] = {
    1: {
        "id": 1,
        "name": "Laptop",
        "price": 999.99,
        "category": "Electronics",
        "stock": 10,
    },
    2: {"id": 2, "name": "Book", "price": 19.99, "category": "Books", "stock": 50},
    3: {"id": 3, "name": "Coffee Mug", "price": 12.99, "category": "Home", "stock": 25},
    4: {
        "id": 4,
        "name": "Smartphone",
        "price": 699.99,
        "category": "Electronics",
        "stock": 5,
    },
    5: {
        "id": 5,
        "name": "T-Shirt",
        "price": 24.99,
        "category": "Clothing",
        "stock": 100,
    },
    6: {
        "id": 6,
        "name": "Wireless Headphones",
        "price": 149.99,
        "category": "Electronics",
        "stock": 15,
    },
    7: {
        "id": 7,
        "name": "Bluetooth Speaker",
        "price": 89.99,
        "category": "Electronics",
        "stock": 20,
    },
    8: {
        "id": 8,
        "name": "Smart Watch",
        "price": 299.99,
        "category": "Electronics",
        "stock": 8,
    },
    9: {
        "id": 9,
        "name": "Tablet",
        "price": 399.99,
        "category": "Electronics",
        "stock": 12,
    },
    10: {
        "id": 10,
        "name": "Gaming Mouse",
        "price": 59.99,
        "category": "Electronics",
        "stock": 30,
    },
    11: {
        "id": 11,
        "name": "Mechanical Keyboard",
        "price": 129.99,
        "category": "Electronics",
        "stock": 18,
    },
    12: {
        "id": 12,
        "name": "USB-C Hub",
        "price": 45.99,
        "category": "Electronics",
        "stock": 25,
    },
    13: {
        "id": 13,
        "name": "Phone Case",
        "price": 19.99,
        "category": "Electronics",
        "stock": 50,
    },
    14: {
        "id": 14,
        "name": "Portable Charger",
        "price": 39.99,
        "category": "Electronics",
        "stock": 40,
    },
    15: {
        "id": 15,
        "name": "Webcam",
        "price": 79.99,
        "category": "Electronics",
        "stock": 22,
    },
}

orders_db: Dict[str, Dict] = {}

users_db: Dict[int, Dict] = {
    1: {
        "id": 1,
        "name": "Alice Johnson",
        "email": "alice@example.com",
        "address": "123 Main St",
    },
    2: {
        "id": 2,
        "name": "Bob Smith",
        "email": "bob@example.com",
        "address": "456 Oak Ave",
    },
    3: {
        "id": 3,
        "name": "Charlie Brown",
        "email": "charlie@example.com",
        "address": "789 Pine Rd",
    },
}


# ============================================================================
# Pydantic Models
# ============================================================================

class Product(BaseModel):
    id: int
    name: str = Field(..., description="Product name")
    price: float = Field(..., gt=0, description="Product price in USD")
    category: str = Field(..., description="Product category")
    stock: int = Field(..., ge=0, description="Available stock quantity")


class CreateProduct(BaseModel):
    name: str = Field(..., description="Product name")
    price: float = Field(..., gt=0, description="Product price in USD")
    category: str = Field(..., description="Product category")
    stock: int = Field(..., ge=0, description="Initial stock quantity")


class UpdateProduct(BaseModel):
    name: Optional[str] = Field(None, description="Updated product name")
    price: Optional[float] = Field(None, gt=0, description="Updated price in USD")
    category: Optional[str] = Field(None, description="Updated category")
    stock: Optional[int] = Field(None, ge=0, description="Updated stock quantity")


class User(BaseModel):
    id: int
    name: str = Field(..., description="User full name")
    email: str = Field(..., description="User email address")
    address: str = Field(..., description="User address")


class OrderItem(BaseModel):
    product_id: int = Field(..., description="Product ID to order")
    quantity: int = Field(..., gt=0, description="Quantity to order")


class CreateOrder(BaseModel):
    items: List[OrderItem] = Field(..., description="List of items to order")


class Order(BaseModel):
    id: str
    user_id: int
    items: List[Dict[str, Any]]
    total: float
    status: str
    created_at: datetime


# ============================================================================
# Helper Functions
# ============================================================================

def generate_order_id() -> str:
    """Generate a random order ID"""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def generate_token() -> str:
    """Generate a secure random token"""
    return secrets.token_urlsafe(32)


# ============================================================================
# Authentication Endpoints
# ============================================================================

@app.post(
    "/auth/login",
    response_model=LoginResponse,
    summary="User Login",
    description="Login with username and password to get an access token",
    tags=["Authentication"],
)
async def login(credentials: LoginRequest = Body(...)):
    """
    Authenticate user and return an access token.
    
    **Test Credentials:**
    - alice / password123
    - bob / password456
    - charlie / password789
    
    The returned token should be used in the Authorization header for protected endpoints:
    `Authorization: Bearer <token>`
    """
    # Verify credentials
    if credentials.username not in user_credentials:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    user_creds = user_credentials[credentials.username]
    
    if user_creds["password"] != credentials.password:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    # Generate token
    token = generate_token()
    user_id = user_creds["user_id"]
    email = user_creds["email"]
    
    # Store token with expiration (24 hours)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    active_tokens[token] = {
        "user_id": user_id,
        "username": credentials.username,
        "email": email,
        "expires_at": expires_at,
    }
    
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        user_id=user_id,
        username=credentials.username,
        expires_in=86400,  # 24 hours in seconds
    )


@app.get(
    "/auth/me",
    response_model=CurrentUser,
    summary="Get Current User",
    description="Get the currently authenticated user's information",
    tags=["Authentication"],
)
async def get_me(user: CurrentUser = Depends(get_current_user)):
    """
    Get the current authenticated user's information.
    
    Requires: Authorization header with Bearer token
    """
    return user


@app.post(
    "/oauth2/token",
    summary="OAuth2 Token Endpoint",
    description="Exchange client credentials for an access token",
    tags=["Authentication"],
)
async def oauth2_token(
    grant_type: str = Form(..., description="Must be 'client_credentials'"),
    client_id: str = Form(..., description="OAuth2 client ID"),
    client_secret: str = Form(..., description="OAuth2 client secret"),
):
    """
    OAuth2 Client Credentials flow token endpoint.
    
    **Test Credentials:**
    - client_id: client_alice, client_secret: secret_alice_12345
    - client_id: client_bob, client_secret: secret_bob_67890
    """
    if grant_type != "client_credentials":
        raise HTTPException(status_code=400, detail="Unsupported grant_type")
    
    if client_id not in oauth2_clients:
        raise HTTPException(status_code=401, detail="Invalid client_id")
    
    client_data = oauth2_clients[client_id]
    
    if client_data["client_secret"] != client_secret:
        raise HTTPException(status_code=401, detail="Invalid client_secret")
    
    # Generate access token
    token = generate_token()
    user_id = client_data["user_id"]
    
    # Get username from user_credentials
    username = None
    email = None
    for user, creds in user_credentials.items():
        if creds["user_id"] == user_id:
            username = user
            email = creds["email"]
            break
    
    if not username:
        raise HTTPException(status_code=401, detail="User not found")
    
    # Store token with expiration (1 hour for OAuth2)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    active_tokens[token] = {
        "user_id": user_id,
        "username": username,
        "email": email,
        "expires_at": expires_at,
    }
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 3600,  # 1 hour in seconds
    }


@app.get(
    "/auth/api-keys",
    summary="List API Keys",
    description="Get available API keys for testing",
    tags=["Authentication"],
)
async def list_api_keys():
    """
    List available test API keys.
    
    Use these keys in the X-API-Key header:
    - sk_test_alice_12345 (for Alice)
    - sk_test_bob_67890 (for Bob)
    """
    return {
        "api_keys": [
            {
                "key": key,
                "user_id": data["user_id"],
                "name": data["name"],
                "active": data["active"],
            }
            for key, data in api_keys.items()
        ]
    }


# ============================================================================
# Public Product Endpoints (No Authentication Required)
# ============================================================================

@app.get("/health", summary="Health Check", description="Check if the API is running")
async def health_check():
    """Simple health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc)}


@app.get(
    "/products",
    response_model=List[Product],
    summary="List Products",
    description="Get all products in the store (public endpoint)",
    tags=["Products"],
)
async def list_products(
    category: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
):
    """
    Get all products, optionally filtered by category and price range.
    
    This is a public endpoint - no authentication required.

    - **category**: Filter by product category
    - **min_price**: Filter products with price >= min_price
    - **max_price**: Filter products with price <= max_price
    """
    products = list(products_db.values())

    if category:
        products = [p for p in products if p["category"].lower() == category.lower()]

    if min_price is not None:
        products = [p for p in products if p["price"] >= min_price]

    if max_price is not None:
        products = [p for p in products if p["price"] <= max_price]

    return products


@app.get(
    "/products/{product_id}",
    response_model=Product,
    summary="Get Product",
    description="Get a specific product by ID (public endpoint)",
    tags=["Products"],
)
async def get_product(product_id: int):
    """
    Get a specific product by its ID.
    
    This is a public endpoint - no authentication required.

    - **product_id**: The ID of the product to retrieve
    """
    if product_id not in products_db:
        raise HTTPException(status_code=404, detail="Product not found")

    return products_db[product_id]


@app.post(
    "/products",
    response_model=Product,
    summary="Create Product",
    description="Create a new product (admin only - protected endpoint)",
    tags=["Products"],
)
async def create_product(product: CreateProduct, user: CurrentUser = Depends(get_current_user)):
    """
    Create a new product in the store.
    
    Requires authentication. In a real app, would check for admin role.

    - **product**: Product details (name, price, category, stock)
    """
    new_id = max(products_db.keys()) + 1 if products_db else 1
    new_product = {"id": new_id, **product.dict()}
    products_db[new_id] = new_product
    return new_product


@app.put(
    "/products/{product_id}",
    response_model=Product,
    summary="Update Product",
    description="Update an existing product (admin only - protected endpoint)",
    tags=["Products"],
)
async def update_product(
    product_id: int,
    product: UpdateProduct,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Update an existing product.
    
    Requires authentication. In a real app, would check for admin role.

    - **product_id**: The ID of the product to update
    - Only provided fields will be updated
    """
    if product_id not in products_db:
        raise HTTPException(status_code=404, detail="Product not found")

    current_product = products_db[product_id]
    update_data = product.dict(exclude_unset=True)

    for field, value in update_data.items():
        current_product[field] = value

    return current_product


@app.delete(
    "/products/{product_id}",
    summary="Delete Product",
    description="Delete a product from the store (admin only - protected endpoint)",
    tags=["Products"],
)
async def delete_product(product_id: int, user: CurrentUser = Depends(get_current_user)):
    """
    Delete a product from the store.
    
    Requires authentication. In a real app, would check for admin role.

    - **product_id**: The ID of the product to delete
    """
    if product_id not in products_db:
        raise HTTPException(status_code=404, detail="Product not found")

    deleted_product = products_db.pop(product_id)
    return {"message": f"Product '{deleted_product['name']}' deleted successfully"}


@app.get(
    "/search",
    summary="Search Products",
    description="Search products by name or category (public endpoint)",
    tags=["Products"],
)
async def search_products(q: str, limit: int = 10):
    """
    Search for products by name or category.
    
    This is a public endpoint - no authentication required.

    - **q**: Search query (searches in product name and category)
    - **limit**: Maximum number of results to return
    """
    query = q.lower()
    results = []

    for product in products_db.values():
        if query in product["name"].lower() or query in product["category"].lower():
            results.append(product)

        if len(results) >= limit:
            break

    return {"query": q, "results": results, "total_found": len(results)}


# ============================================================================
# Protected Order Endpoints (Authentication Required)
# ============================================================================

@app.get(
    "/orders",
    response_model=List[Order],
    summary="List User's Orders",
    description="Get all orders for the currently authenticated user",
    tags=["Orders"],
)
async def list_user_orders(user: CurrentUser = Depends(get_current_user)):
    """
    Get all orders for the currently authenticated user.
    
    Users can only see their own orders.
    
    Requires: Authorization header with Bearer token
    """
    user_orders = [order for order in orders_db.values() if order["user_id"] == user.id]
    return user_orders


@app.get(
    "/orders/{order_id}",
    response_model=Order,
    summary="Get User's Order",
    description="Get a specific order by ID (only if it belongs to the current user)",
    tags=["Orders"],
)
async def get_user_order(order_id: str, user: CurrentUser = Depends(get_current_user)):
    """
    Get a specific order by its ID.
    
    Users can only access their own orders. Returns 404 if order doesn't exist
    or belongs to another user.
    
    Requires: Authorization header with Bearer token

    - **order_id**: The ID of the order to retrieve
    """
    if order_id not in orders_db:
        raise HTTPException(status_code=404, detail="Order not found")

    order = orders_db[order_id]
    
    # Check if order belongs to the current user
    if order["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="You don't have permission to access this order")

    return order


@app.post(
    "/orders",
    response_model=Order,
    summary="Create Order",
    description="Create a new order for the currently authenticated user",
    tags=["Orders"],
)
async def create_user_order(order: CreateOrder, user: CurrentUser = Depends(get_current_user)):
    """
    Create a new order for the currently authenticated user.
    
    The order is automatically associated with the current user's ID.
    
    Requires: Authorization header with Bearer token

    - **items**: List of products and quantities to order
    """
    # Calculate total and validate items
    total = 0.0
    order_items = []

    for item in order.items:
        if item.product_id not in products_db:
            raise HTTPException(status_code=404, detail=f"Product {item.product_id} not found")

        product = products_db[item.product_id]

        if product["stock"] < item.quantity:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Insufficient stock for product {product['name']}. "
                    f"Available: {product['stock']}, Requested: {item.quantity}"
                ),
            )

        item_total = product["price"] * item.quantity
        total += item_total

        order_items.append(
            {
                "product_id": item.product_id,
                "product_name": product["name"],
                "quantity": item.quantity,
                "unit_price": product["price"],
                "item_total": item_total,
            }
        )

        # Update stock
        products_db[item.product_id]["stock"] -= item.quantity

    # Create order for the current user
    order_id = generate_order_id()
    new_order = {
        "id": order_id,
        "user_id": user.id,  # Order belongs to the current user
        "items": order_items,
        "total": total,
        "status": "pending",
        "created_at": datetime.now(timezone.utc),
    }

    orders_db[order_id] = new_order
    return new_order


@app.put(
    "/orders/{order_id}/status",
    summary="Update Order Status",
    description="Update the status of an order (only if it belongs to the current user)",
    tags=["Orders"],
)
async def update_user_order_status(
    order_id: str,
    status: str,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Update the status of an order.
    
    Users can only update their own orders. Returns 403 if order belongs to another user.
    
    Requires: Authorization header with Bearer token

    - **order_id**: The ID of the order to update
    - **status**: New status (pending, confirmed, shipped, delivered, cancelled)
    """
    if order_id not in orders_db:
        raise HTTPException(status_code=404, detail="Order not found")

    order = orders_db[order_id]
    
    # Check if order belongs to the current user
    if order["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="You don't have permission to update this order")

    valid_statuses = ["pending", "confirmed", "shipped", "delivered", "cancelled"]
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Valid options: {valid_statuses}")

    orders_db[order_id]["status"] = status
    return {"message": f"Order {order_id} status updated to {status}"}


# ============================================================================
# Analytics Endpoints
# ============================================================================

@app.get(
    "/analytics/summary",
    summary="Analytics Summary",
    description="Get summary analytics for the store (public endpoint)",
    tags=["Analytics"],
)
async def analytics_summary():
    """
    Get summary analytics including product counts, order stats, and revenue.
    
    This is a public endpoint - no authentication required.
    """
    total_products = len(products_db)
    total_orders = len(orders_db)
    total_revenue = sum(order["total"] for order in orders_db.values())

    # Category breakdown
    categories = {}
    for product in products_db.values():
        cat = product["category"]
        if cat not in categories:
            categories[cat] = {"count": 0, "total_stock": 0}
        categories[cat]["count"] += 1
        categories[cat]["total_stock"] += product["stock"]

    # Order status breakdown
    order_statuses = {}
    for order in orders_db.values():
        status = order["status"]
        order_statuses[status] = order_statuses.get(status, 0) + 1

    return {
        "products": {"total": total_products, "by_category": categories},
        "orders": {
            "total": total_orders,
            "by_status": order_statuses,
            "total_revenue": total_revenue,
        },
        "users": {"total": len(users_db)},
    }


@app.get(
    "/analytics/my-orders",
    summary="My Orders Analytics",
    description="Get analytics for the current user's orders",
    tags=["Analytics"],
)
async def my_orders_analytics(user: CurrentUser = Depends(get_current_user)):
    """
    Get analytics for the currently authenticated user's orders.
    
    Requires: Authorization header with Bearer token
    """
    user_orders = [order for order in orders_db.values() if order["user_id"] == user.id]
    
    total_orders = len(user_orders)
    total_spent = sum(order["total"] for order in user_orders)
    
    # Order status breakdown
    order_statuses = {}
    for order in user_orders:
        status = order["status"]
        order_statuses[status] = order_statuses.get(status, 0) + 1
    
    return {
        "total_orders": total_orders,
        "total_spent": total_spent,
        "by_status": order_statuses,
        "username": user.username,
    }


# ============================================================================
# Add Bedrock Chat with Authentication Support
# ============================================================================

# Add Bedrock chat capabilities with authentication enabled
bedrock_chat = add_bedrock_chat(
    app,
    # Enable authentication for tool calls
    enable_tool_auth=True,
    supported_auth_types=[
        "bearer_token",
        "basic_auth",
        "api_key",
        "oauth2_client_credentials",
        "custom",
    ],
    # Expose order endpoints (restricted to authenticated users in API)
    allowed_paths=[
        "/orders",
        "/auth",
        "/products",
        "/analytics",
        "/search",
        "/health",
    ],
    excluded_paths=["/docs", "/redoc", "/openapi.json", "/chat", "/ws"],
)


if __name__ == "__main__":
    import uvicorn

    print("🚀 Starting E-commerce API with Multiple Authentication Methods and AI Chat")
    print("📖 API Documentation: http://localhost:8000/docs")
    print("💬 AI Chat Interface: http://localhost:8000/chat")
    print("")
    print("🔐 Test Credentials for Multiple Auth Methods:")
    print("")
    print("1. Bearer Token (JWT-style):")
    print("   POST /auth/login with:")
    print("   - alice / password123")
    print("   - bob / password456")
    print("   - charlie / password789")
    print("")
    print("2. HTTP Basic Authentication:")
    print("   Username: alice, Password: password123")
    print("   Username: bob, Password: password456")
    print("   Username: charlie, Password: password789")
    print("")
    print("3. API Key (X-API-Key header):")
    print("   - sk_test_alice_12345 (for Alice)")
    print("   - sk_test_bob_67890 (for Bob)")
    print("   GET /auth/api-keys to see all keys")
    print("")
    print("4. OAuth2 Client Credentials:")
    print("   POST /oauth2/token with:")
    print("   - client_id: client_alice, client_secret: secret_alice_12345")
    print("   - client_id: client_bob, client_secret: secret_bob_67890")
    print("")
    print("Then use the token for authenticated requests:")
    print("   Authorization: Bearer <token>")
    print("Or use X-API-Key header for API key auth")
    print("Or use HTTP Basic auth in the Authorization header")
    print("")
    print("Try the AI chat via the UI and test different auth methods!")
    print("")

    uvicorn.run("app_auth:app", host="0.0.0.0", port=8000, reload=True, log_level="info")


# Custom OpenAPI schema to include security definitions and requirements
def custom_openapi():
    """Generate custom OpenAPI schema with security requirements for protected endpoints"""
    if app.openapi_schema:
        return app.openapi_schema
    
    openapi_schema = get_openapi(
        title="Example E-commerce API with Authentication",
        version="1.0.0",
        description="E-commerce API with AI chat assistance and token-based authentication",
        routes=app.routes,
    )
    
    # Preserve existing components (schemas, etc.) and add security scheme
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}
    
    if "securitySchemes" not in openapi_schema["components"]:
        openapi_schema["components"]["securitySchemes"] = {}
    
    # Add security scheme
    openapi_schema["components"]["securitySchemes"]["HTTPBearer"] = {
        "type": "http",
        "scheme": "bearer",
        "description": "Bearer token authentication. Get token from /auth/login",
    }
    
    # Also add basic auth scheme
    openapi_schema["components"]["securitySchemes"]["HTTPBasic"] = {
        "type": "http",
        "scheme": "basic",
        "description": "HTTP Basic authentication with username and password",
    }
    
    # List of endpoints that require authentication
    protected_endpoints = [
        "/orders",
        "/auth/me",
        "/analytics/my-orders",
    ]
    
    # Add security requirements to protected endpoints
    for path, path_item in openapi_schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method in ["get", "post", "put", "delete", "patch"]:
                # Check if this is a protected endpoint
                is_protected = any(
                    path.startswith(protected) for protected in protected_endpoints
                )
                
                # Exclude specific public operations
                if path == "/products" and method == "get":
                    # GET /products is public, but POST/PUT/DELETE are protected
                    is_protected = False
                if path.startswith("/products/") and method == "get":
                    is_protected = False
                if path == "/search" or path == "/health":
                    is_protected = False
                if path == "/analytics/summary":
                    is_protected = False
                if path == "/auth/login":
                    is_protected = False
                
                # Add security to protected endpoints
                if is_protected and method != "options":
                    if "security" not in operation:
                        operation["security"] = [{"HTTPBearer": []}, {"HTTPBasic": []}]
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi
