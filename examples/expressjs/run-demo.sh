#!/bin/bash

# Complete Express.js + Bedrock Integration Demo
# This script sets up and runs the complete example

echo "🚀 Express.js + Bedrock Integration Demo"
echo "========================================"

# Check if we're in the right directory
if [ ! -f "server.js" ]; then
    echo "❌ Please run this script from the examples/expressjs/ directory"
    echo "   cd examples/expressjs/"
    echo "   ./run-demo.sh"
    exit 1
fi

# Check if Node.js is installed
if ! command -v node &> /dev/null; then
    echo "❌ Node.js is not installed"
    echo "   Please install Node.js from https://nodejs.org/"
    exit 1
fi

# Check if npm is installed
if ! command -v npm &> /dev/null; then
    echo "❌ npm is not installed"
    echo "   Please install npm (usually comes with Node.js)"
    exit 1
fi

echo "1. Installing Node.js dependencies..."
if [ ! -d "node_modules" ]; then
    npm install
    if [ $? -ne 0 ]; then
        echo "❌ Failed to install dependencies"
        exit 1
    fi
    echo "✅ Dependencies installed"
else
    echo "✅ Dependencies already installed"
fi

echo ""
echo "2. Starting Express server..."
echo "   Server will run on http://localhost:3000"
echo "   Press Ctrl+C to stop the server"
echo ""
echo "🌐 Available endpoints:"
echo "   • API Documentation: http://localhost:3000/api-docs"
echo "   • OpenAPI Spec: http://localhost:3000/api_spec.json"
echo "   • API Root: http://localhost:3000/"
echo ""
echo "🤖 To test Python integration:"
echo "   # In another terminal, from this directory:"
echo "   poetry run python integration.py"
echo ""

# Start the Express server
node server.js