// express-server.js
// Sample Express.js server that generates the OpenAPI spec used in the Python integration example

const express = require('express');
const swaggerJsdoc = require('swagger-jsdoc');
const swaggerUi = require('swagger-ui-express');
const fs = require('fs');
const path = require('path');

const app = express();

// Middleware
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// In-memory data stores (for demo purposes)
let users = [
    { id: 1, name: 'John Doe', email: 'john@example.com', created_at: '2024-01-01T00:00:00Z' },
    { id: 2, name: 'Jane Smith', email: 'jane@example.com', created_at: '2024-01-02T00:00:00Z' },
];

let products = [
    { id: 1, name: 'Laptop Pro', description: 'High-performance laptop', price: 1299.99, category: 'Electronics', in_stock: true },
    { id: 2, name: 'Coffee Mug', description: 'Ceramic coffee mug', price: 15.99, category: 'Home', in_stock: true },
    { id: 3, name: 'Wireless Mouse', description: 'Bluetooth wireless mouse', price: 29.99, category: 'Electronics', in_stock: false },
];

let orders = [];
let nextUserId = 3;
let nextOrderId = 1;

/**
 * @swagger
 * components:
 *   schemas:
 *     User:
 *       type: object
 *       required:
 *         - name
 *         - email
 *       properties:
 *         id:
 *           type: integer
 *           description: User ID
 *         name:
 *           type: string
 *           description: User's full name
 *         email:
 *           type: string
 *           format: email
 *           description: User's email address
 *         created_at:
 *           type: string
 *           format: date-time
 *           description: User creation timestamp
 *     Product:
 *       type: object
 *       properties:
 *         id:
 *           type: integer
 *           description: Product ID
 *         name:
 *           type: string
 *           description: Product name
 *         description:
 *           type: string
 *           description: Product description
 *         price:
 *           type: number
 *           description: Product price
 *         category:
 *           type: string
 *           description: Product category
 *         in_stock:
 *           type: boolean
 *           description: Whether product is in stock
 *     Order:
 *       type: object
 *       properties:
 *         order_id:
 *           type: integer
 *           description: Order ID
 *         user_id:
 *           type: integer
 *           description: ID of user who placed the order
 *         total_amount:
 *           type: number
 *           description: Total order amount
 *         status:
 *           type: string
 *           description: Order status
 *         created_at:
 *           type: string
 *           format: date-time
 *           description: Order creation timestamp
 */

/**
 * @swagger
 * /api/v1/users:
 *   get:
 *     summary: Get all users
 *     description: Retrieve a list of all users in the system
 *     parameters:
 *       - name: limit
 *         in: query
 *         description: Maximum number of users to return
 *         schema:
 *           type: integer
 *           minimum: 1
 *           maximum: 100
 *           default: 10
 *       - name: offset
 *         in: query
 *         description: Number of users to skip
 *         schema:
 *           type: integer
 *           minimum: 0
 *           default: 0
 *     responses:
 *       200:
 *         description: List of users
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 users:
 *                   type: array
 *                   items:
 *                     $ref: '#/components/schemas/User'
 *                 total:
 *                   type: integer
 *   post:
 *     summary: Create a new user
 *     description: Create a new user account
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required:
 *               - name
 *               - email
 *             properties:
 *               name:
 *                 type: string
 *                 description: User's full name
 *                 minLength: 1
 *               email:
 *                 type: string
 *                 format: email
 *                 description: User's email address
 *               age:
 *                 type: integer
 *                 minimum: 18
 *                 maximum: 120
 *                 description: User's age
 *     responses:
 *       201:
 *         description: User created successfully
 *         content:
 *           application/json:
 *             schema:
 *               $ref: '#/components/schemas/User'
 *       400:
 *         description: Bad request - validation error
 */
app.get('/api/v1/users', (req, res) => {
    const limit = Math.min(parseInt(req.query.limit) || 10, 100);
    const offset = parseInt(req.query.offset) || 0;
    
    const paginatedUsers = users.slice(offset, offset + limit);
    
    res.json({
        users: paginatedUsers,
        total: users.length,
        limit: limit,
        offset: offset
    });
});

app.post('/api/v1/users', (req, res) => {
    const { name, email, age } = req.body;
    
    // Validation
    if (!name || !email) {
        return res.status(400).json({
            error: 'Name and email are required',
            details: { name: !name ? 'Required' : null, email: !email ? 'Required' : null }
        });
    }
    
    // Check for duplicate email
    if (users.find(u => u.email === email)) {
        return res.status(400).json({
            error: 'Email already exists',
            details: { email: 'Must be unique' }
        });
    }
    
    const newUser = {
        id: nextUserId++,
        name,
        email,
        age: age || null,
        created_at: new Date().toISOString()
    };
    
    users.push(newUser);
    res.status(201).json(newUser);
});

/**
 * @swagger
 * /api/v1/users/{userId}:
 *   get:
 *     summary: Get user by ID
 *     description: Retrieve a specific user by their ID
 *     parameters:
 *       - name: userId
 *         in: path
 *         required: true
 *         description: User ID
 *         schema:
 *           type: integer
 *     responses:
 *       200:
 *         description: User details
 *         content:
 *           application/json:
 *             schema:
 *               $ref: '#/components/schemas/User'
 *       404:
 *         description: User not found
 */
app.get('/api/v1/users/:userId', (req, res) => {
    const userId = parseInt(req.params.userId);
    const user = users.find(u => u.id === userId);
    
    if (!user) {
        return res.status(404).json({
            error: 'User not found',
            userId: userId
        });
    }
    
    res.json(user);
});

/**
 * @swagger
 * /api/v1/products:
 *   get:
 *     summary: Get all products
 *     description: Retrieve a list of all products
 *     parameters:
 *       - name: category
 *         in: query
 *         description: Filter by product category
 *         schema:
 *           type: string
 *       - name: min_price
 *         in: query
 *         description: Minimum price filter
 *         schema:
 *           type: number
 *           minimum: 0
 *       - name: max_price
 *         in: query
 *         description: Maximum price filter
 *         schema:
 *           type: number
 *           minimum: 0
 *       - name: in_stock
 *         in: query
 *         description: Filter by stock availability
 *         schema:
 *           type: boolean
 *     responses:
 *       200:
 *         description: List of products
 *         content:
 *           application/json:
 *             schema:
 *               type: array
 *               items:
 *                 $ref: '#/components/schemas/Product'
 */
app.get('/api/v1/products', (req, res) => {
    let filteredProducts = [...products];
    
    // Apply filters
    const { category, min_price, max_price, in_stock } = req.query;
    
    if (category) {
        filteredProducts = filteredProducts.filter(p => 
            p.category.toLowerCase().includes(category.toLowerCase())
        );
    }
    
    if (min_price !== undefined) {
        const minPrice = parseFloat(min_price);
        if (!isNaN(minPrice)) {
            filteredProducts = filteredProducts.filter(p => p.price >= minPrice);
        }
    }
    
    if (max_price !== undefined) {
        const maxPrice = parseFloat(max_price);
        if (!isNaN(maxPrice)) {
            filteredProducts = filteredProducts.filter(p => p.price <= maxPrice);
        }
    }
    
    if (in_stock !== undefined) {
        const stockFilter = in_stock === 'true';
        filteredProducts = filteredProducts.filter(p => p.in_stock === stockFilter);
    }
    
    res.json(filteredProducts);
});

/**
 * @swagger
 * /api/v1/orders:
 *   post:
 *     summary: Create a new order
 *     description: Create a new order for a user
 *     requestBody:
 *       required: true
 *       content:
 *         application/json:
 *           schema:
 *             type: object
 *             required:
 *               - user_id
 *               - items
 *               - shipping_address
 *             properties:
 *               user_id:
 *                 type: integer
 *                 description: ID of the user placing the order
 *               items:
 *                 type: array
 *                 description: Items in the order
 *                 items:
 *                   type: object
 *                   required:
 *                     - product_id
 *                     - quantity
 *                   properties:
 *                     product_id:
 *                       type: integer
 *                       description: Product ID
 *                     quantity:
 *                       type: integer
 *                       minimum: 1
 *                       description: Quantity of the product
 *               shipping_address:
 *                 type: object
 *                 required:
 *                   - street
 *                   - city
 *                   - state
 *                   - zip_code
 *                 properties:
 *                   street:
 *                     type: string
 *                   city:
 *                     type: string
 *                   state:
 *                     type: string
 *                   zip_code:
 *                     type: string
 *     responses:
 *       201:
 *         description: Order created successfully
 *         content:
 *           application/json:
 *             schema:
 *               $ref: '#/components/schemas/Order'
 *       400:
 *         description: Bad request - validation error
 *       404:
 *         description: User or product not found
 */
app.post('/api/v1/orders', (req, res) => {
    const { user_id, items, shipping_address } = req.body;
    
    // Validation
    if (!user_id || !items || !shipping_address) {
        return res.status(400).json({
            error: 'user_id, items, and shipping_address are required'
        });
    }
    
    // Check if user exists
    const user = users.find(u => u.id === user_id);
    if (!user) {
        return res.status(404).json({
            error: 'User not found',
            user_id: user_id
        });
    }
    
    // Validate items and calculate total
    let totalAmount = 0;
    const orderItems = [];
    
    for (const item of items) {
        const product = products.find(p => p.id === item.product_id);
        if (!product) {
            return res.status(404).json({
                error: 'Product not found',
                product_id: item.product_id
            });
        }
        
        if (!product.in_stock) {
            return res.status(400).json({
                error: 'Product out of stock',
                product_id: item.product_id,
                product_name: product.name
            });
        }
        
        const itemTotal = product.price * item.quantity;
        totalAmount += itemTotal;
        
        orderItems.push({
            product_id: product.id,
            product_name: product.name,
            quantity: item.quantity,
            unit_price: product.price,
            total_price: itemTotal
        });
    }
    
    const newOrder = {
        order_id: nextOrderId++,
        user_id: user_id,
        user_name: user.name,
        items: orderItems,
        shipping_address: shipping_address,
        total_amount: Math.round(totalAmount * 100) / 100, // Round to 2 decimal places
        status: 'pending',
        created_at: new Date().toISOString()
    };
    
    orders.push(newOrder);
    res.status(201).json(newOrder);
});

// Internal/admin endpoints (should be excluded from AI tools)
/**
 * @swagger
 * /internal/admin/users:
 *   get:
 *     summary: Admin - Get all users with sensitive data
 *     description: Internal admin endpoint - should be excluded from AI tools
 *     responses:
 *       200:
 *         description: Admin user data with sensitive information
 */
app.get('/internal/admin/users', (req, res) => {
    // This endpoint would contain sensitive admin data
    res.json({
        users: users.map(u => ({
            ...u,
            internal_id: `admin_${u.id}`,
            permissions: ['read', 'write'],
            last_login: new Date().toISOString()
        })),
        admin_notes: 'This is sensitive admin data that should not be exposed to AI'
    });
});

/**
 * @swagger
 * /internal/health:
 *   get:
 *     summary: Internal health check
 *     description: Internal health check - should be excluded from AI tools
 *     responses:
 *       200:
 *         description: Health status
 */
app.get('/internal/health', (req, res) => {
    res.json({
        status: 'healthy',
        timestamp: new Date().toISOString(),
        uptime: process.uptime(),
        memory: process.memoryUsage(),
        internal_metrics: 'This contains internal system metrics'
    });
});

// Swagger configuration
const swaggerOptions = {
    definition: {
        openapi: '3.0.0',
        info: {
            title: 'Express E-commerce API',
            version: '1.0.0',
            description: 'Sample Express.js e-commerce API for Bedrock integration demo',
        },
        servers: [
            {
                url: 'http://localhost:3000',
                description: 'Development server'
            }
        ],
    },
    apis: ['./server.js'], // Path to this file for swagger-jsdoc to scan
};

// Generate OpenAPI specification
const specs = swaggerJsdoc(swaggerOptions);

// Serve Swagger UI
app.use('/api-docs', swaggerUi.serve, swaggerUi.setup(specs));

// Save OpenAPI spec to file (for use with Python integration)
const specPath = path.join(__dirname, 'api_spec.json');
fs.writeFileSync(specPath, JSON.stringify(specs, null, 2));

// Root endpoint with API information
app.get('/', (req, res) => {
    res.json({
        message: 'Express E-commerce API',
        version: '1.0.0',
        documentation: '/api-docs',
        openapi_spec: '/api_spec.json',
        endpoints: {
            users: '/api/v1/users',
            products: '/api/v1/products',
            orders: '/api/v1/orders'
        },
        sample_requests: {
            get_users: 'GET /api/v1/users?limit=5',
            get_products: 'GET /api/v1/products?category=Electronics',
            create_user: 'POST /api/v1/users',
            create_order: 'POST /api/v1/orders'
        }
    });
});

// Serve the generated OpenAPI spec file
app.get('/api_spec.json', (req, res) => {
    res.sendFile(specPath);
});

// Error handling middleware
app.use((err, req, res, next) => {
    console.error(err.stack);
    res.status(500).json({
        error: 'Something went wrong!',
        message: err.message
    });
});

// 404 handler
app.use((req, res) => {
    res.status(404).json({
        error: 'Endpoint not found',
        path: req.path,
        method: req.method,
        available_endpoints: [
            'GET /',
            'GET /api/v1/users',
            'POST /api/v1/users',
            'GET /api/v1/users/{id}',
            'GET /api/v1/products',
            'POST /api/v1/orders',
            'GET /api-docs'
        ]
    });
});

// Start server
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
    console.log(`🚀 Express server running on port ${PORT}`);
    console.log(`📝 API Documentation: http://localhost:${PORT}/api-docs`);
    console.log(`🔧 OpenAPI Spec: http://localhost:${PORT}/api_spec.json`);
    console.log(`✓ OpenAPI spec file saved: ${specPath}`);
    console.log(`\n📊 Available endpoints:`);
    console.log(`   • GET    /api/v1/users`);
    console.log(`   • POST   /api/v1/users`);
    console.log(`   • GET    /api/v1/users/{id}`);
    console.log(`   • GET    /api/v1/products`);
    console.log(`   • POST   /api/v1/orders`);
    console.log(`\n🤖 Ready for Bedrock integration!`);
});

module.exports = app;