#!/usr/bin/env node

/**
 * Standalone script to generate OpenAPI spec from Express server
 * This can be used in CI/CD pipelines or as a build step
 */

const swaggerJsdoc = require('swagger-jsdoc');
const fs = require('fs');
const path = require('path');

// Swagger configuration (matches the one in express-server.js)
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
    apis: ['./server.js'], // Path to the server file for swagger-jsdoc to scan
};

try {
    // Generate OpenAPI specification
    console.log('🔧 Generating OpenAPI specification...');
    const specs = swaggerJsdoc(swaggerOptions);

    // Save to file
    const specPath = path.join(__dirname, 'api_spec.json');
    fs.writeFileSync(specPath, JSON.stringify(specs, null, 2));

    console.log(`✅ OpenAPI spec generated: ${specPath}`);
    console.log(`📊 Generated ${Object.keys(specs.paths || {}).length} endpoints`);
    console.log(`🔗 API Base URL: ${specs.servers[0].url}`);

    // Show summary of generated endpoints
    if (specs.paths) {
        console.log('\n📋 Generated Endpoints:');
        for (const [path, methods] of Object.entries(specs.paths)) {
            const methodList = Object.keys(methods).filter(m => m !== 'parameters').join(', ').toUpperCase();
            console.log(`   ${methodList.padEnd(12)} ${path}`);
        }
    }

} catch (error) {
    console.error('❌ Error generating OpenAPI spec:', error.message);
    process.exit(1);
}
