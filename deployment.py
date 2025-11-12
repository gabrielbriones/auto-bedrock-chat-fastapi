"""Development and deployment utilities"""

version = "1.0.0"

# Example Kubernetes deployment
kubernetes_deployment = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bedrock-chat-api
  labels:
    app: bedrock-chat-api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: bedrock-chat-api
  template:
    metadata:
      labels:
        app: bedrock-chat-api
    spec:
      containers:
      - name: api
        image: bedrock-chat-api:latest
        ports:
        - containerPort: 8000
        env:
        - name: AWS_REGION
          value: "us-east-1"
        - name: BEDROCK_MODEL_ID
          value: "anthropic.claude-3-5-sonnet-20241022-v2:0"
        - name: CHAT_ENABLE_UI
          value: "true"
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 5
        livenessProbe:
          httpGet:
            path: /api/chat/health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: bedrock-chat-api-service
spec:
  selector:
    app: bedrock-chat-api
  ports:
  - protocol: TCP
    port: 80
    targetPort: 8000
  type: LoadBalancer
"""

# Docker Compose for development
docker_compose = """
version: '3.8'

services:
  bedrock-chat-api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - AWS_REGION=us-east-1
      - BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0
      - CHAT_ENABLE_UI=true
      - CHAT_LOG_LEVEL=info
    volumes:
      - ~/.aws:/home/appuser/.aws:ro
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  # Optional: Add Redis for session storage in production
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    restart: unless-stopped
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data

volumes:
  redis_data:
"""

print(f"Auto Bedrock Chat FastAPI v{version}")
print("Development utilities loaded")
