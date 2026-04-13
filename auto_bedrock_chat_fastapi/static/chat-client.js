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

        this.currentPromptCache = {};       // Cache of resolved placeholder values, e.g. { JOB_ID: '...', PLATFORM: '...' }
        this.pendingPromptTemplate = null;  // Template waiting for one or more variable values

        this.setupEventListeners();
        this._setupVariablePanel();
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
        let wsUrl = `${protocol}//${window.location.host}${window.CONFIG.websocketUrl}`;

        // Append SSO session token as query param if available (auto-authenticates the WS session)
        if (window._ssoSessionToken) {
            wsUrl += (wsUrl.includes('?') ? '&' : '?') + 'session_token=' + encodeURIComponent(window._ssoSessionToken);
        }

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
            this.pendingPromptTemplate = null;
            const variablePanel = document.getElementById('variablePanel');
            if (variablePanel) variablePanel.classList.add('hidden');

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
        document.querySelectorAll('.preset-prompt-btn').forEach(btn => { btn.disabled = false; });
    }

    _disablePresetButtons() {
        document.querySelectorAll('.preset-prompt-btn').forEach(btn => { btn.disabled = true; });
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
            btn.addEventListener('click', () => this._handlePresetPrompt(prompt));
            bar.appendChild(btn);
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

    // Validate a single placeholder value.
    // Variables whose name ends with _ID must be valid UUIDs; others must be non-empty.
    _validateVar(varName, value) {
        const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
        if (varName.endsWith('_ID')) return UUID_RE.test(value.trim());
        return value.trim().length > 0;
    }

    _handlePresetPrompt(prompt) {
        const template = prompt.template || '';
        const allVars = this._getPlaceholders(template);

        if (allVars.length === 0) {
            this._sendPresetMessage(template);
            return;
        }

        const missingVars = allVars.filter(v => !this.currentPromptCache[v]);

        if (missingVars.length === 0) {
            // All placeholders already resolved from cache
            const resolved = allVars.reduce(
                (t, v) => t.replaceAll(`{{${v}}}`, this.currentPromptCache[v]),
                template
            );
            this._sendPresetMessage(resolved);
            return;
        }

        // Show the inline panel to collect the missing variable values
        this.pendingPromptTemplate = template;
        this._showVariablePanel(missingVars);
    }

    _showVariablePanel(vars) {
        const panel = document.getElementById('variablePanel');
        const container = document.getElementById('variableInputs');
        if (!panel || !container) return;

        container.innerHTML = '';
        vars.forEach(varName => {
            const row = document.createElement('div');
            row.className = 'variable-row';

            const label = document.createElement('label');
            label.htmlFor = `varInput_${varName}`;
            label.textContent = `${this._prettifyVarName(varName)}:`;

            const input = document.createElement('input');
            input.type = 'text';
            input.id = `varInput_${varName}`;
            input.dataset.varName = varName;
            if (varName.endsWith('_ID')) {
                input.placeholder = 'e.g. e62f2481-b56e-4d2a-9c16-11cd8db76caa';
            }

            row.appendChild(label);
            row.appendChild(input);
            container.appendChild(row);
        });

        panel.classList.remove('hidden');
        container.querySelector('input')?.focus();
    }

    _sendPresetMessage(text) {
        if (!text || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
        this.addMessage('user', text);
        this.ws.send(JSON.stringify({ type: 'chat', message: text }));
    }

    _setupVariablePanel() {
        const panel     = document.getElementById('variablePanel');
        const submitBtn = document.getElementById('varPanelSubmit');
        const cancelBtn = document.getElementById('varPanelCancel');

        if (!panel || !submitBtn || !cancelBtn) return;

        const doSubmit = () => {
            const inputs = panel.querySelectorAll('input[data-var-name]');
            let allValid = true;
            const submittedValues = {};

            inputs.forEach(input => {
                const varName = input.dataset.varName;
                const value   = input.value.trim();
                if (!this._validateVar(varName, value)) {
                    input.classList.add('input-error');
                    allValid = false;
                } else {
                    input.classList.remove('input-error');
                    submittedValues[varName] = value;
                }
            });

            if (!allValid) {
                panel.querySelector('.input-error')?.focus();
                return;
            }

            // Persist only JOB_ID to the long-lived cache so subsequent preset
            // prompts can reuse the current-job context without re-asking.
            // Template-specific vars (e.g. NEW_JOB_ID) are intentionally not
            // cached so the panel always prompts for fresh values on each use.
            if ('JOB_ID' in submittedValues) {
                this.currentPromptCache['JOB_ID'] = submittedValues['JOB_ID'];
            }

            panel.classList.add('hidden');
            if (this.pendingPromptTemplate) {
                const allVars = this._getPlaceholders(this.pendingPromptTemplate);
                // Merge long-lived cache (JOB_ID from history) with freshly
                // submitted values; submittedValues takes precedence so the
                // user can override a cached value when it appears in the panel.
                const values = { ...this.currentPromptCache, ...submittedValues };
                const resolved = allVars.reduce(
                    (t, v) => t.replaceAll(`{{${v}}}`, values[v]),
                    this.pendingPromptTemplate
                );
                this.pendingPromptTemplate = null;
                this._sendPresetMessage(resolved);
            }
        };

        submitBtn.addEventListener('click', doSubmit);

        // Delegate key events for dynamically-created inputs
        panel.addEventListener('keydown', (e) => {
            if (e.target.tagName !== 'INPUT') return;
            if (e.key === 'Enter')  { e.preventDefault(); doSubmit(); }
            if (e.key === 'Escape') { cancelBtn.click(); }
        });
        panel.addEventListener('input', (e) => {
            if (e.target.tagName === 'INPUT') e.target.classList.remove('input-error');
        });

        cancelBtn.addEventListener('click', () => {
            this.pendingPromptTemplate = null;
            panel.classList.add('hidden');
        });
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
            // Logout: send logout message and clear auth
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

        // Keep the prompt cache up to date with values mentioned in free-form messages.
        // UUIDs are stored as JOB_ID so that subsequent preset prompts referencing
        // {{JOB_ID}} can be resolved without asking the user again.
        const uuidMatch = message.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
        if (uuidMatch) {
            this.currentPromptCache['JOB_ID'] = uuidMatch[0];
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
                this.authenticated = true;
                this.addMessage('system', `🔐 Authenticated with ${data.auth_type}`);
                // Show display name for SSO in header
                if (data.auth_type === 'sso' && data.display_name) {
                    const userDisplay = document.getElementById('ssoUserDisplay');
                    if (userDisplay) {
                        userDisplay.textContent = data.display_name;
                        userDisplay.style.display = 'inline';
                    }
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
                // Clear SSO session token on logout
                window._ssoSessionToken = null;
                this.addMessage('system', '🔓 Logged out successfully.');
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
                // SSO session expired — clear token and prompt re-login
                window._ssoSessionToken = null;
                this.authenticated = false;
                this.authPayload = null;
                this.authSent = false;
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
