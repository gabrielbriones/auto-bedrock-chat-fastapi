// Chat client with auth support
class ChatClient {
    constructor(authPayload = null) {
        this.ws = null;
        this.authPayload = authPayload;
        this.authenticated = false;  // True only after server confirms auth_configured
        this.authSent = false;
        this.intentionalClose = false;
        this.connecting = false;
        this.messageInput = document.getElementById('messageInput');
        this.sendButton = document.getElementById('sendButton');
        this.authButton = document.getElementById('authButton');
        this.chatMessages = document.getElementById('chatMessages');
        this.connectionStatus = document.getElementById('connectionStatus');
        this.typingIndicator = document.getElementById('typingIndicator');
        this.typingText = document.getElementById('typingText');

        // Variable definitions keyed by name, built from window.CONFIG.variables.
        // Values live in the always-visible DOM inputs — no hidden JS cache.
        this._variableDefs = {};
        (window.CONFIG.variables || []).forEach(v => {
            this._variableDefs[v.name] = v;
        });

        this.setupEventListeners();
        this._renderVariablesSection();
        this.updateAuthButtonUI();  // Update button on page load (reflects current auth state)
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
        // Prevent multiple simultaneous connections using synchronous flag
        if (this.connecting) {
            console.log('Connection already in progress, skipping connect()');
            return;
        }

        // Set flag immediately to prevent race conditions
        this.connecting = true;

        if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) {
            console.log('WebSocket already connecting/connected, skipping connect()');
            this.connecting = false; // Reset flag since we're not proceeding
            return;
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}${window.CONFIG.websocketUrl}`;
        // SSO session token is delivered via an HttpOnly cookie that the
        // browser sends automatically on the WebSocket handshake — no need
        // to include it in the URL.

        console.log('Creating new WebSocket connection...');
        this.ws = new WebSocket(wsUrl);

        // Reset auth state for the new connection — the server creates a fresh
        // session that knows nothing about previous authentication.  Credentials
        // will be re-sent in onopen if authPayload is set, and input will only
        // be enabled once the server confirms via auth_configured.
        this.authSent = false;
        this.authenticated = false;
        this.updateAuthButtonUI();

        this.ws.onopen = (event) => {
            console.log('Connected to chat');
            this.connecting = false;
            this.updateConnectionStatus(true);

            // Re-send authentication on every new connection if credentials exist
            if (this.authPayload) {
                this.sendAuth();
            } else if (!window.CONFIG.requireAuth) {
                this.enableInput();
            }
        };

        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this.handleMessage(data);
        };

        this.ws.onclose = (event) => {
            console.log(`WebSocket closed. Intentional: ${this.intentionalClose}`);
            this.connecting = false;
            this.updateConnectionStatus(false);
            this.messageInput.disabled = true;
            this.sendButton.disabled = true;
            this._disablePresetButtons();

            // Re-enable auth submit button if the modal is still open
            // (server never replied with auth_configured / auth_failed)
            this._recoverAuthSubmitButton();

            // Only reconnect if close wasn't intentional (e.g., not from logout)
            if (!this.intentionalClose) {
                console.log('Scheduling reconnect in 3 seconds...');
                setTimeout(() => this.connect(), 3000);
            } else {
                console.log('Intentional close, not reconnecting');
                // Reset flag for next connection
                this.intentionalClose = false;
            }
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.connecting = false;
            this.addMessage('system', 'Connection error occurred');

            // Re-enable auth submit button if the modal is still open
            this._recoverAuthSubmitButton();
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
        this._renderPresetButtons();
        this._updatePresetButtonStates();
    }

    _disablePresetButtons() {
        document.querySelectorAll('.preset-prompt-btn').forEach(btn => { btn.disabled = true; });
    }

    _renderVariablesSection() {
        const section = document.getElementById('presetVariablesSection');
        if (!section || Object.keys(this._variableDefs).length === 0) return;

        for (const [name, def] of Object.entries(this._variableDefs)) {
            const row = document.createElement('div');
            row.className = 'variable-input-row';

            const label = document.createElement('label');
            label.htmlFor = `var_${name}`;
            label.textContent = def.label || this._prettifyVarName(name);

            const el = this._createVariableInput(name, def);
            const eventName = (def.input_type === 'select' || def.input_type === 'checkbox') ? 'change' : 'input';
            el.addEventListener(eventName, () => this._updatePresetButtonStates());

            row.appendChild(label);
            row.appendChild(el);
            section.appendChild(row);
        }
    }

    _createVariableInput(name, def) {
        const type = def.input_type || 'text';

        if (type === 'select') {
            const select = document.createElement('select');
            select.id = `var_${name}`;
            select.dataset.varName = name;
            if (!def.default) {
                const empty = document.createElement('option');
                empty.value = '';
                empty.textContent = def.placeholder || `Select ${def.label || name}…`;
                select.appendChild(empty);
            }
            (def.options || []).forEach(opt => {
                const option = document.createElement('option');
                if (typeof opt === 'string') {
                    option.value = opt;
                    option.textContent = opt;
                } else {
                    option.value = opt.value;
                    option.textContent = opt.label;
                }
                if (def.default && option.value === def.default) option.selected = true;
                select.appendChild(option);
            });
            return select;
        }

        if (type === 'checkbox') {
            const input = document.createElement('input');
            input.type = 'checkbox';
            input.id = `var_${name}`;
            input.dataset.varName = name;
            input.checked = def.default === 'true';
            return input;
        }

        // text or number
        const input = document.createElement('input');
        input.type = type;
        input.id = `var_${name}`;
        input.dataset.varName = name;
        if (def.placeholder) input.placeholder = def.placeholder;
        if (def.default)     input.value = def.default;
        if (type === 'number') {
            if (def.min  != null) input.min  = def.min;
            if (def.max  != null) input.max  = def.max;
            if (def.step != null) input.step = def.step;
        }
        return input;
    }

    _renderPresetButtons() {
        const bar = document.getElementById('presetPromptsBar');
        if (!bar || bar.dataset.rendered) return;  // render only once
        bar.dataset.rendered = 'true';

        const prompts = window.CONFIG.presetPrompts || [];
        prompts.forEach(prompt => {
            const btn = document.createElement('button');
            btn.className = 'preset-prompt-btn';
            btn.textContent = prompt.label || 'Prompt';
            if (prompt.description) btn.title = prompt.description;

            const requiredVars = this._getPlaceholders(prompt.template || '');
            requiredVars.forEach(varName => {
                const tag = document.createElement('span');
                tag.className = 'preset-var-tag';
                tag.textContent = varName;
                btn.appendChild(tag);
            });
            btn.dataset.requiredVars = JSON.stringify(requiredVars);
            btn.addEventListener('click', () => this._handlePresetClick(prompt));
            bar.appendChild(btn);
        });
        this._updatePresetButtonStates();
    }

    _getVarValue(varName) {
        const el = document.getElementById(`var_${varName}`);
        if (!el) return '';
        const def = this._variableDefs[varName];
        if (def && def.input_type === 'checkbox') return el.checked ? 'true' : 'false';
        return el.value.trim();
    }

    _updatePresetButtonStates() {
        document.querySelectorAll('.preset-prompt-btn').forEach(btn => {
            const required = JSON.parse(btn.dataset.requiredVars || '[]');
            btn.disabled = !required.every(name => this._validateVar(name, this._getVarValue(name)));
        });
    }

    // Return the list of unique placeholder names found in a template string.
    _getPlaceholders(template) {
        const re = /\{\{(\w+)\}\}/g;
        const found = new Set();
        let m;
        while ((m = re.exec(template)) !== null) found.add(m[1]);
        return [...found];
    }

    // Prettify a SCREAMING_SNAKE_CASE variable name for display.
    // e.g. JOB_ID → "Job ID",  PLATFORM → "Platform"
    _prettifyVarName(name) {
        return name.split('_')
            .map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
            .join(' ');
    }

    // Definition-driven validation.
    _validateVar(varName, value) {
        const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
        const def  = this._variableDefs[varName];
        const type = def?.input_type || 'text';
        const trimmed = (typeof value === 'string') ? value.trim() : String(value);

        if (type === 'checkbox') return true;
        if (type === 'select')   return trimmed.length > 0;

        if (type === 'number') {
            if (trimmed.length === 0) return false;
            const num = Number(trimmed);
            if (isNaN(num)) return false;
            if (def?.min != null && num < def.min) return false;
            if (def?.max != null && num > def.max) return false;
            return true;
        }

        // text: use validate field when present, else fall back to name convention
        if (def?.validate === 'uuid')    return UUID_RE.test(trimmed);
        if (def?.validate === 'nonempty') return trimmed.length > 0;
        if (def?.validate) {
            try {
                return new RegExp(def.validate).test(trimmed);
            } catch (e) {
                console.warn(`Invalid validate pattern for variable "${varName}":`, e);
                return false;
            }
        }

        // backwards-compat fallback (no definition)
        if (varName.endsWith('_ID')) return UUID_RE.test(trimmed);
        return trimmed.length > 0;
    }

    _handlePresetClick(prompt) {
        const template = prompt.template || '';
        const vars = this._getPlaceholders(template);
        const resolved = vars.reduce(
            (t, name) => t.replaceAll(`{{${name}}}`, this._getVarValue(name)),
            template
        );
        this._sendPresetMessage(resolved);
    }

    _sendPresetMessage(text) {
        if (!text || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
        this.addMessage('user', text);
        this.ws.send(JSON.stringify({ type: 'chat', message: text }));
    }

    _recoverAuthSubmitButton() {
        const authModal = document.getElementById('authModal');
        const authSubmitBtn = document.querySelector('.auth-submit');
        if (authModal && !authModal.classList.contains('hidden') && authSubmitBtn && authSubmitBtn.disabled) {
            authSubmitBtn.disabled = false;
            authSubmitBtn.textContent = 'Authenticate';
        }
    }

    handleAuthButtonClick() {
        if (this.authenticated) {
            // SSO logout: POST to the HTTP logout endpoint to clear the
            // HttpOnly cookie and server-side session, then reload.
            if (window.CONFIG.ssoAuthenticated) {
                const logoutUrl = (window.CONFIG.ssoLoginUrl || '').replace('/login', '/logout');
                fetch(logoutUrl, {
                    method: 'POST',
                    credentials: 'same-origin',
                }).then(function() {
                    window.location.reload();
                });
                return;
            }

            // Non-SSO logout: send logout message over WebSocket and clear auth
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({
                    type: 'logout'
                }));
            }
            this.authPayload = null;
            this.authenticated = false;
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
        if (this.authenticated) {
            this.authButton.textContent = 'Log out';
            this.authButton.classList.add('logout');
        } else {
            this.authButton.textContent = 'Log in';
            this.authButton.classList.remove('logout');
            // Clear SSO user display on logout
            const userDisplay = document.getElementById('ssoUserDisplay');
            if (userDisplay) {
                userDisplay.textContent = '';
                userDisplay.style.display = 'none';
            }
        }
    }

    sendMessage() {
        const message = this.messageInput.value.trim();
        if (!message || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return;
        }

        // Auto-detect: run each variable's detect_pattern against the sent message
        // and populate the corresponding input if matched.
        for (const [name, def] of Object.entries(this._variableDefs)) {
            if (!def.detect_pattern) continue;
            if (def.input_type && def.input_type !== 'text') continue;
            let re;
            try {
                re = new RegExp(def.detect_pattern, def.detect_flags || 'i');
            } catch (e) {
                console.warn(`Invalid detect_pattern for variable "${name}":`, e);
                continue;
            }
            const match = message.match(re);
            if (match) {
                const input = document.getElementById(`var_${name}`);
                if (input) {
                    input.value = match[0];
                    input.dispatchEvent(new Event('input'));
                }
            }
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
                console.log('Received auth_configured:', data);
                this.authenticated = true;
                this.addMessage('system', `🔐 ${data.message || `Authenticated with ${data.auth_type}`}`);
                // Show display name in header if provided (works for SSO and other auth types)
                if (data.display_name) {
                    console.log('Setting display name:', data.display_name);
                    const userDisplay = document.getElementById('ssoUserDisplay');
                    if (userDisplay) {
                        userDisplay.textContent = data.display_name;
                        userDisplay.style.display = 'inline';
                        console.log('Display name set successfully');
                    } else {
                        console.error('ssoUserDisplay element not found');
                    }
                } else {
                    console.log('No display_name in auth_configured message');
                }
                this.updateAuthButtonUI();  // Update button after auth
                this.enableInput();
                // Re-enable auth submit button for future use (e.g. after logout)
                const authSubmitBtnOk = document.querySelector('.auth-submit');
                if (authSubmitBtnOk) {
                    authSubmitBtnOk.disabled = false;
                    authSubmitBtnOk.textContent = 'Authenticate';
                }
                // Hide auth modal now that server confirmed credentials
                const authModal = document.getElementById('authModal');
                if (authModal) authModal.classList.add('hidden');
                break;

            case 'auth_failed':
                this.authenticated = false;
                this.addMessage('system', `❌ Authentication failed: ${data.message}`);
                // Clear auth state so button shows "Log in"
                this.authPayload = null;
                this.authSent = false;
                this.updateAuthButtonUI();
                // Only enable input if auth is not required
                if (!window.CONFIG.requireAuth) {
                    this.enableInput();
                }
                // Re-enable the auth submit button for retry
                const authSubmitBtn = document.querySelector('.auth-submit');
                if (authSubmitBtn) {
                    authSubmitBtn.disabled = false;
                    authSubmitBtn.textContent = 'Authenticate';
                }
                // Re-show auth modal so user can retry
                const authModalRetry = document.getElementById('authModal');
                if (authModalRetry) {
                    authModalRetry.classList.remove('hidden');
                    initializeAuthModal();
                }
                break;

            case 'logout_success':
                this.authenticated = false;
                this.addMessage('system', '🔓 Logged out successfully.');
                // Clear user display name from header
                const userDisplay = document.getElementById('ssoUserDisplay');
                if (userDisplay) {
                    userDisplay.textContent = '';
                    userDisplay.style.display = 'none';
                }
                this.updateAuthButtonUI();  // Update button after logout
                // Disable input if auth is required
                if (window.CONFIG.requireAuth) {
                    this.messageInput.disabled = true;
                    this.sendButton.disabled = true;
                    this._disablePresetButtons();
                }
                // Close connection after logout - mark as intentional to prevent auto-reconnect
                // Set flag BEFORE checking/closing to avoid race conditions
                this.intentionalClose = true;
                if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                    console.log('Logout: closing connection (intentional close flag already set)');
                    this.ws.close();
                }
                break;

            case 'connection_established':
                this.addMessage('system', `Connected! Session ID: ${data.session_id}`);
                if (!window.CONFIG.requireAuth || this.authenticated) {
                    this.enableInput();
                } else {
                    // Ensure input stays disabled when auth is required but user hasn't authenticated
                    this.messageInput.disabled = true;
                    this.sendButton.disabled = true;
                }
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

            case 'auth_expired':
                // SSO session expired — prompt re-login
                this.authenticated = false;
                this.authPayload = null;
                this.authSent = false;
                // Clear user display name from header
                const userDisplayExpired = document.getElementById('ssoUserDisplay');
                if (userDisplayExpired) {
                    userDisplayExpired.textContent = '';
                    userDisplayExpired.style.display = 'none';
                }
                this.intentionalClose = true;
                if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                    this.ws.close();
                }
                this.addMessage('system', `⏰ ${data.message || 'Session expired. Please log in again.'}`);
                this.updateAuthButtonUI();
                // Show auth modal with SSO type pre-selected if SSO is configured
                if (window.CONFIG.ssoEnabled) {
                    const authModal = document.getElementById('authModal');
                    if (authModal) {
                        authModal.classList.remove('hidden');
                        initializeAuthModal();
                        const authTypeSelect = document.getElementById('authType');
                        if (authTypeSelect) {
                            authTypeSelect.value = 'sso';
                            updateAuthFields();
                        }
                    }
                }
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
        const messageText = this._normalizeContent(content);

        // Process content based on role and model
        if (role === 'assistant') {
            // Process content with markdown and reasoning removal
            const processedContent = processMessageContent(messageText, window.CONFIG.modelId);
            contentDiv.innerHTML = processedContent;
        } else if (role === 'user') {
            // Render user messages as markdown so preset prompts (with headers,
            // tables, lists) are readable instead of a wall of plain text.
            // DOMPurify sanitizes the marked output to prevent XSS from raw HTML
            // that marked passes through by default.
            if (window.marked) {
                const raw = marked.parse(messageText);
                contentDiv.innerHTML = window.DOMPurify ? DOMPurify.sanitize(raw) : raw;
            } else {
                contentDiv.textContent = messageText;
            }
        } else {
            // system messages — plain text only
            contentDiv.textContent = messageText;
        }

        // Add tool calls information if present
        if (toolCalls && toolCalls.length > 0) {
            const toolCallsDiv = document.createElement('div');
            toolCallsDiv.className = 'tool-calls';

            const titleStrong = document.createElement('strong');
            titleStrong.textContent = 'API Calls:';
            toolCallsDiv.appendChild(titleStrong);
            toolCallsDiv.appendChild(document.createElement('br'));

            toolCalls.forEach(call => {
                const callDiv = document.createElement('div');
                callDiv.className = 'tool-call';

                const nameSpan = document.createElement('span');
                nameSpan.className = 'tool-call-name';
                nameSpan.textContent = call.name;

                const argsText = document.createTextNode(`(${JSON.stringify(call.arguments)})`);

                callDiv.appendChild(nameSpan);
                callDiv.appendChild(argsText);
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
        // Auto-scroll to bottom when typing indicator appears
        this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
    }

    hideTypingIndicator() {
        this.typingIndicator.classList.remove('active');
    }

    _normalizeContent(content) {
        if (typeof content === 'string') {
            return content;
        }
        if (content === null || content === undefined) {
            return '';
        }
        if (typeof content === 'object') {
            // Safely serialize objects, filtering out sensitive properties
            return this._safeStringify(content);
        }
        return String(content);
    }

    _safeStringify(obj) {
        // List of potentially sensitive property names to exclude
        const sensitiveKeys = [
            'password', 'token', 'secret', 'apiKey', 'api_key',
            'authorization', 'credentials', 'private', 'key',
            'stack', 'stackTrace', '__proto__', 'constructor'
        ];

        try {
            // Use replacer function to filter sensitive data
            return JSON.stringify(obj, (key, value) => {
                // Check if key is sensitive (case-insensitive)
                if (sensitiveKeys.some(sk => key.toLowerCase().includes(sk.toLowerCase()))) {
                    return '[REDACTED]';
                }
                // Exclude functions and symbols
                if (typeof value === 'function' || typeof value === 'symbol') {
                    return undefined;
                }
                return value;
            }, 2); // Pretty print with 2-space indentation
        } catch (e) {
            // Handle circular references or other stringify errors
            return '[Object: Unable to serialize safely]';
        }
    }
}
