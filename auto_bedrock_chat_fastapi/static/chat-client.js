// Chat client with auth support
class ChatClient {
    constructor(authPayload = null) {
        this.ws = null;
        this.authPayload = authPayload;
        this.authSent = false;
        this.messageInput = document.getElementById('messageInput');
        this.sendButton = document.getElementById('sendButton');
        this.authButton = document.getElementById('authButton');
        this.chatMessages = document.getElementById('chatMessages');
        this.connectionStatus = document.getElementById('connectionStatus');
        this.typingIndicator = document.getElementById('typingIndicator');
        this.typingText = document.getElementById('typingText');

        this.setupEventListeners();
        this.updateAuthButtonUI();  // Update button on page load
        this.connect();
    }

    setupEventListeners() {
        this.sendButton.addEventListener('click', () => this.sendMessage());
        this.authButton.addEventListener('click', () => this.handleAuthButtonClick());
        this.messageInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Auto-resize textarea as user types
        this.messageInput.addEventListener('input', () => {
            this.messageInput.style.height = 'auto';
            this.messageInput.style.height = this.messageInput.scrollHeight + 'px';

            // Limit max height to prevent excessive growth
            const maxHeight = 150;
            if (this.messageInput.scrollHeight > maxHeight) {
                this.messageInput.style.height = maxHeight + 'px';
                this.messageInput.style.overflowY = 'auto';
            } else {
                this.messageInput.style.overflowY = 'hidden';
            }
        });
    }

    connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}${window.CONFIG.websocketUrl}`;

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = (event) => {
            console.log('Connected to chat');
            this.updateConnectionStatus(true);

            // Send authentication if provided
            if (this.authPayload && !this.authSent) {
                this.sendAuth();
            } else {
                this.enableInput();
            }
        };

        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this.handleMessage(data);
        };

        this.ws.onclose = (event) => {
            console.log('Disconnected from chat');
            this.updateConnectionStatus(false);
            this.messageInput.disabled = true;
            this.sendButton.disabled = true;

            // Try to reconnect after 3 seconds
            setTimeout(() => this.connect(), 3000);
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.addMessage('system', 'Connection error occurred');
        };
    }

    sendAuth() {
        if (this.authPayload && this.ws && this.ws.readyState === WebSocket.OPEN) {
            console.log('Sending authentication...');
            this.ws.send(JSON.stringify(this.authPayload));
            this.authSent = true;
        }
    }

    updateConnectionStatus(connected) {
        this.connectionStatus.textContent = connected ? 'Connected' : 'Disconnected';
        this.connectionStatus.className = `connection-status ${connected ? 'connected' : 'disconnected'}`;
    }

    enableInput() {
        this.messageInput.disabled = false;
        this.sendButton.disabled = false;
    }

    handleAuthButtonClick() {
        if (this.authPayload) {
            // Logout: send logout message and clear auth
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({
                    type: 'logout'
                }));
            }
            this.authPayload = null;
            this.authSent = false;
            // Send logout message to server - it will respond with logout_success
            // which triggers connection close in handleMessage()

            // Show auth modal for re-authentication
            const authModal = document.getElementById('authModal');
            if (authModal) {
                authModal.classList.remove('hidden');
                initializeAuthModal();
            }
            // Don't add message here - backend will send logout_success
        } else {
            // Login: show auth modal
            document.getElementById('authModal').classList.remove('hidden');
            initializeAuthModal();  // Auto-select single auth type if needed
        }
    }

    updateAuthButtonUI() {
        if (this.authPayload) {
            this.authButton.textContent = 'Log out';
            this.authButton.classList.add('logout');
        } else {
            this.authButton.textContent = 'Log in';
            this.authButton.classList.remove('logout');
        }
    }

    sendMessage() {
        const message = this.messageInput.value.trim();
        if (!message || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return;
        }

        // Add user message to chat
        this.addMessage('user', message);

        // Send to server
        this.ws.send(JSON.stringify({
            type: 'chat',
            message: message
        }));

        // Clear input and reset height
        this.messageInput.value = '';
        this.messageInput.style.height = 'auto';
        this.messageInput.style.height = '48px';  // Reset to min height
        this.messageInput.style.overflowY = 'hidden';
    }

    handleMessage(data) {
        switch (data.type) {
            case 'auth_configured':
                this.addMessage('system', `🔐 Authenticated with ${data.auth_type}`);
                this.updateAuthButtonUI();  // Update button after auth
                this.enableInput();
                break;

            case 'logout_success':
                this.addMessage('system', '🔓 Logged out successfully.');
                this.updateAuthButtonUI();  // Update button after logout
                // Close connection after logout - it will reconnect when user logs back in
                if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                    this.ws.close();
                }
                break;

            case 'connection_established':
                this.addMessage('system', `Connected! Session ID: ${data.session_id}`);
                this.enableInput();
                break;

            case 'typing':
                this.showTypingIndicator(data.message || 'AI is typing...');
                break;

            case 'ai_response':
                this.hideTypingIndicator();
                this.addMessage('assistant', data.message, data.tool_calls, data.tool_results);
                break;

            case 'error':
                this.hideTypingIndicator();
                this.addMessage('system', `Error: ${data.message}`);
                break;

            case 'pong':
                // Handle ping/pong if needed
                break;
        }
    }

    addMessage(role, content, toolCalls, toolResults) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${role}`;

        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';

        // Ensure content is a string
        const messageText = typeof content === 'string' ? content :
                          typeof content === 'object' ? JSON.stringify(content) :
                          content ?? '';

        // Process content based on role and model
        if (role === 'assistant') {
            // Process content with markdown and reasoning removal
            const processedContent = processMessageContent(messageText, window.CONFIG.modelId);
            contentDiv.innerHTML = processedContent;
        } else {
            // For user and system messages, use plain text
            contentDiv.textContent = messageText;
        }

        // Add tool calls information if present
        if (toolCalls && toolCalls.length > 0) {
            const toolCallsDiv = document.createElement('div');
            toolCallsDiv.className = 'tool-calls';
            toolCallsDiv.innerHTML = '<strong>API Calls:</strong><br>';

            toolCalls.forEach(call => {
                const callDiv = document.createElement('div');
                callDiv.className = 'tool-call';
                callDiv.innerHTML = `<span class="tool-call-name">${call.name}</span>` +
                                  `(${JSON.stringify(call.arguments)})`;
                toolCallsDiv.appendChild(callDiv);
            });

            contentDiv.appendChild(toolCallsDiv);
        }

        messageDiv.appendChild(contentDiv);
        this.chatMessages.appendChild(messageDiv);
        this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
    }

    showTypingIndicator(message = 'AI is typing...') {
        this.typingText.textContent = message;
        this.typingIndicator.classList.add('active');
    }

    hideTypingIndicator() {
        this.typingIndicator.classList.remove('active');
    }
}
