"""Example FastAPI application demonstrating auto-bedrock-chat-fastapi"""

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import asyncio
from datetime import datetime, timedelta
import random
import string

# Import the plugin
from auto_bedrock_chat_fastapi import add_bedrock_chat
from auto_bedrock_chat_fastapi.config import load_config

# Create FastAPI app
app = FastAPI(
    title="Example E-commerce API",
    description="A sample e-commerce API with AI chat assistance",
    version="1.0.0"
)

# Mock database
products_db: Dict[int, Dict] = {
    1: {"id": 1, "name": "Laptop", "price": 999.99, "category": "Electronics", "stock": 10},
    2: {"id": 2, "name": "Book", "price": 19.99, "category": "Books", "stock": 50},
    3: {"id": 3, "name": "Coffee Mug", "price": 12.99, "category": "Home", "stock": 25},
    4: {"id": 4, "name": "Smartphone", "price": 699.99, "category": "Electronics", "stock": 5},
    5: {"id": 5, "name": "T-Shirt", "price": 24.99, "category": "Clothing", "stock": 100},
    # Add more electronics under $500 for demo
    6: {"id": 6, "name": "Wireless Headphones", "price": 149.99, "category": "Electronics", "stock": 15},
    7: {"id": 7, "name": "Bluetooth Speaker", "price": 89.99, "category": "Electronics", "stock": 20},
    8: {"id": 8, "name": "Smart Watch", "price": 299.99, "category": "Electronics", "stock": 8},
    9: {"id": 9, "name": "Tablet", "price": 399.99, "category": "Electronics", "stock": 12},
    10: {"id": 10, "name": "Gaming Mouse", "price": 59.99, "category": "Electronics", "stock": 30},
    11: {"id": 11, "name": "Mechanical Keyboard", "price": 129.99, "category": "Electronics", "stock": 18},
    12: {"id": 12, "name": "USB-C Hub", "price": 45.99, "category": "Electronics", "stock": 25},
    13: {"id": 13, "name": "Phone Case", "price": 19.99, "category": "Electronics", "stock": 50},
    14: {"id": 14, "name": "Portable Charger", "price": 39.99, "category": "Electronics", "stock": 40},
    15: {"id": 15, "name": "Webcam", "price": 79.99, "category": "Electronics", "stock": 22}
}

orders_db: Dict[str, Dict] = {}
users_db: Dict[int, Dict] = {
    1: {"id": 1, "name": "John Doe", "email": "john@example.com", "address": "123 Main St"},
    2: {"id": 2, "name": "Jane Smith", "email": "jane@example.com", "address": "456 Oak Ave"}
}

# Pydantic models
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
    user_id: int = Field(..., description="User ID placing the order")
    items: List[OrderItem] = Field(..., description="List of items to order")

class Order(BaseModel):
    id: str
    user_id: int
    items: List[Dict[str, Any]]
    total: float
    status: str
    created_at: datetime

# Helper functions
def generate_order_id() -> str:
    """Generate a random order ID"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

# API Routes

# Health check
@app.get("/health", summary="Health Check", description="Check if the API is running")
async def health_check():
    """Simple health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.utcnow()}

# Product endpoints
@app.get("/products", response_model=List[Product], summary="List Products", 
         description="Get all products in the store")
async def list_products(
    category: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None
):
    """
    Get all products, optionally filtered by category and price range.
    
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

@app.get("/products/{product_id}", response_model=Product, summary="Get Product",
         description="Get a specific product by ID")
async def get_product(product_id: int):
    """
    Get a specific product by its ID.
    
    - **product_id**: The ID of the product to retrieve
    """
    if product_id not in products_db:
        raise HTTPException(status_code=404, detail="Product not found")
    
    return products_db[product_id]

@app.post("/products", response_model=Product, summary="Create Product",
          description="Create a new product")
async def create_product(product: CreateProduct):
    """
    Create a new product in the store.
    
    Requires product name, price, category, and initial stock.
    """
    new_id = max(products_db.keys()) + 1 if products_db else 1
    new_product = {
        "id": new_id,
        **product.dict()
    }
    products_db[new_id] = new_product
    return new_product

@app.put("/products/{product_id}", response_model=Product, summary="Update Product",
         description="Update an existing product")
async def update_product(product_id: int, product: UpdateProduct):
    """
    Update an existing product.
    
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

@app.delete("/products/{product_id}", summary="Delete Product",
            description="Delete a product from the store")
async def delete_product(product_id: int):
    """
    Delete a product from the store.
    
    - **product_id**: The ID of the product to delete
    """
    if product_id not in products_db:
        raise HTTPException(status_code=404, detail="Product not found")
    
    deleted_product = products_db.pop(product_id)
    return {"message": f"Product '{deleted_product['name']}' deleted successfully"}

# User endpoints
@app.get("/users", response_model=List[User], summary="List Users",
         description="Get all users")
async def list_users():
    """Get all registered users."""
    return list(users_db.values())

@app.get("/users/{user_id}", response_model=User, summary="Get User",
         description="Get a specific user by ID")
async def get_user(user_id: int):
    """
    Get a specific user by their ID.
    
    - **user_id**: The ID of the user to retrieve
    """
    if user_id not in users_db:
        raise HTTPException(status_code=404, detail="User not found")
    
    return users_db[user_id]

# Order endpoints
@app.post("/orders", response_model=Order, summary="Create Order",
          description="Create a new order")
async def create_order(order: CreateOrder):
    """
    Create a new order for a user.
    
    - **user_id**: ID of the user placing the order
    - **items**: List of products and quantities to order
    """
    if order.user_id not in users_db:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Calculate total and validate items
    total = 0.0
    order_items = []
    
    for item in order.items:
        if item.product_id not in products_db:
            raise HTTPException(
                status_code=404, 
                detail=f"Product {item.product_id} not found"
            )
        
        product = products_db[item.product_id]
        
        if product["stock"] < item.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient stock for product {product['name']}. Available: {product['stock']}, Requested: {item.quantity}"
            )
        
        item_total = product["price"] * item.quantity
        total += item_total
        
        order_items.append({
            "product_id": item.product_id,
            "product_name": product["name"],
            "quantity": item.quantity,
            "unit_price": product["price"],
            "item_total": item_total
        })
        
        # Update stock
        products_db[item.product_id]["stock"] -= item.quantity
    
    # Create order
    order_id = generate_order_id()
    new_order = {
        "id": order_id,
        "user_id": order.user_id,
        "items": order_items,
        "total": total,
        "status": "pending",
        "created_at": datetime.utcnow()
    }
    
    orders_db[order_id] = new_order
    return new_order

@app.get("/orders/{order_id}", response_model=Order, summary="Get Order",
         description="Get a specific order by ID")
async def get_order(order_id: str):
    """
    Get a specific order by its ID.
    
    - **order_id**: The ID of the order to retrieve
    """
    if order_id not in orders_db:
        raise HTTPException(status_code=404, detail="Order not found")
    
    return orders_db[order_id]

@app.get("/orders", response_model=List[Order], summary="List Orders",
         description="Get all orders, optionally filtered by user")
async def list_orders(user_id: Optional[int] = None):
    """
    Get all orders, optionally filtered by user ID.
    
    - **user_id**: Filter orders by user ID
    """
    orders = list(orders_db.values())
    
    if user_id is not None:
        orders = [o for o in orders if o["user_id"] == user_id]
    
    return orders

@app.put("/orders/{order_id}/status", summary="Update Order Status",
         description="Update the status of an order")
async def update_order_status(order_id: str, status: str):
    """
    Update the status of an order.
    
    - **order_id**: The ID of the order to update
    - **status**: New status (pending, confirmed, shipped, delivered, cancelled)
    """
    if order_id not in orders_db:
        raise HTTPException(status_code=404, detail="Order not found")
    
    valid_statuses = ["pending", "confirmed", "shipped", "delivered", "cancelled"]
    if status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Valid options: {valid_statuses}"
        )
    
    orders_db[order_id]["status"] = status
    return {"message": f"Order {order_id} status updated to {status}"}

# Search endpoint
@app.get("/search", summary="Search Products",
         description="Search products by name or category")
async def search_products(q: str, limit: int = 10):
    """
    Search for products by name or category.
    
    - **q**: Search query (searches in product name and category)
    - **limit**: Maximum number of results to return
    """
    query = q.lower()
    results = []
    
    for product in products_db.values():
        if (query in product["name"].lower() or 
            query in product["category"].lower()):
            results.append(product)
        
        if len(results) >= limit:
            break
    
    return {
        "query": q,
        "results": results,
        "total_found": len(results)
    }

# Analytics endpoints
@app.get("/analytics/summary", summary="Analytics Summary",
         description="Get summary analytics for the store")
async def analytics_summary():
    """
    Get summary analytics including product counts, order stats, and revenue.
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
        "products": {
            "total": total_products,
            "by_category": categories
        },
        "orders": {
            "total": total_orders,
            "by_status": order_statuses,
            "total_revenue": total_revenue
        },
        "users": {
            "total": len(users_db)
        }
    }

# Add Bedrock chat capabilities using .env configuration
# Most configuration comes from .env file
# We override the list fields here due to Pydantic v2 limitations with .env list parsing
bedrock_chat = add_bedrock_chat(
    app,
    # These list fields need to be set in code (Pydantic v2 limitation)
    allowed_paths=[
        "/products",
        "/users", 
        "/orders",
        "/search",
        "/analytics",
        "/health"
    ],
    excluded_paths=[
        "/docs",
        "/redoc", 
        "/openapi.json",
        "/chat",
        "/ws",
        "/api/chat"
    ]
    # All other settings (model_id, temperature, endpoints, etc.) come from .env
)

if __name__ == "__main__":
    import uvicorn
    
    print("🚀 Starting Example E-commerce API with AI Chat")
    print("📖 API Documentation: http://localhost:8000/docs")
    print("💬 AI Chat Interface: http://localhost:8000/chat")
    print("🔗 WebSocket Chat: ws://localhost:8000/ws/chat")
    print("📊 Chat Health: http://localhost:8000/api/chat/health")
    
    uvicorn.run(
        "example_app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )