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

        // Feedback: track message_ids the user has already rated this session
        // so we render the submitted indicator instead of the buttons on
        // re-renders (history reload, etc.). Mirrored to sessionStorage so
        // the state survives in-page re-renders within the same tab.
        this._feedbackStorageKey = 'feedback.submitted';
        this._submittedFeedback = this._loadSubmittedFeedback();

        // Lock-while-responding state: tracks whether we are waiting for an
        // assistant response so the input can be disabled mid-turn.
        this.awaitingResponse = false;

        // Conversation sidebar: activeConversationId is the
        // LangGraph thread_id for whatever conversation this connection is
        // currently "in" — kept separate from any WebSocket session/connection
        // identifier. null until a conversation is created (first chat message)
        // or explicitly loaded from the sidebar.
        this._conversationSidebarEnabled = !!window.CONFIG.conversationPersistenceEnabled;
        this.activeConversationId = null;
        this.conversations = [];
        // True while a first turn was sent but we haven't yet learned its
        // conversation_id (either because the server hasn't replied yet, or
        // because the connection dropped mid-turn before it could). Used to
        // auto-select the recovered conversation on reconnect instead of
        // leaving the sidebar with nothing selected — see
        // _maybeRecoverPendingConversation().
        this._awaitingConversationId = false;
        this.conversationSidebar = document.getElementById('conversationSidebar');
        this.conversationList = document.getElementById('conversationList');
        this.newChatButton = document.getElementById('newChatButton');
        this.sidebarToggleButton = document.getElementById('sidebarToggleButton');
        this.sidebarCloseButton = document.getElementById('sidebarCloseButton');
        this.sidebarBackdrop = document.getElementById('sidebarBackdrop');
        // Multi-select state for bulk deletion: ids the user has checked via
        // the per-item checkboxes rendered by _renderConversationList().
        this._selectedConversationIds = new Set();
        // Ids of the bulk delete currently awaiting a server reply (null when
        // none is in flight) -- doubles as the double-submit guard.
        this._pendingBulkDeleteIds = null;
        this.conversationSelectAllCheckbox = document.getElementById('conversationSelectAllCheckbox');
        this.conversationBulkDeleteBar = document.getElementById('conversationBulkDeleteBar');
        this.conversationBulkDeleteCount = document.getElementById('conversationBulkDeleteCount');
        this.conversationBulkDeleteButton = document.getElementById('conversationBulkDeleteButton');
        this._setupConversationSidebarListeners();

        // Dynamic parameter overrides settings sidebar.
        this._configSidebarEnabled = !!window.CONFIG.enableConfigSidebar;
        this._allowedDynamicOverrides = window.CONFIG.allowedDynamicOverrides || null;
        this._activeConfigOverrides = {};
        this.configSidebarToggleButton = document.getElementById('configSidebarToggleButton');
        this.configSidebar = document.getElementById('configSidebar');
        this.configSidebarCloseButton = document.getElementById('configSidebarCloseButton');
        this.configSidebarBackdrop = document.getElementById('configSidebarBackdrop');
        this.configSidebarBody = document.getElementById('configSidebarBody');
        this.configResetButton = document.getElementById('configResetButton');
        this.configOverrideBadge = document.getElementById('configOverrideBadge');
        this.modelIdDisplay = document.getElementById('modelIdDisplay');
        this._setupConfigSidebarListeners();

        // Deep-link support: ?prompt=<id>&VAR=value... lets an external
        // link pre-fill (and, by default, auto-send) a preset prompt once
        // the chat becomes usable. Parsed once from the initial URL and
        // applied at most once per page load — see _applyDeepLink().
        this._deepLink = this._parseDeepLink();
        this._deepLinkApplied = false;

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
                if (this.awaitingResponse) return;
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
            this.awaitingResponse = false;
            // A response the client was waiting on can no longer arrive on
            // this (now closed) connection — don't leave the spinner/typing
            // text stuck forever. If the turn actually completes server-side,
            // it'll be picked up via _maybeRecoverPendingConversation() once
            // the reconnect's conversation_list arrives.
            this.hideTypingIndicator();
            this.messageInput.disabled = true;
            this.messageInput.placeholder = 'Type your message...';
            this.messageInput.classList.remove('input-locked');
            this.sendButton.disabled = true;
            this._disablePresetButtons();
            // Same reasoning for an in-flight bulk delete: its reply can't
            // arrive on this connection, so release the guard rather than
            // wedging the delete button until a page reload.
            this._pendingBulkDeleteIds = null;

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
        // Don't override the response-lock — it is layered on top of
        // connection-level enable/disable.
        if (this.awaitingResponse && window.CONFIG.lockInputWhileResponding) return;
        this.messageInput.disabled = false;
        this.sendButton.disabled = false;
        this._renderPresetButtons();
        this._updatePresetButtonStates();
        this._applyDeepLink();
    }

    _disablePresetButtons() {
        document.querySelectorAll('.preset-prompt-btn').forEach(btn => { btn.disabled = true; });
    }

    _lockInputForResponse() {
        if (!window.CONFIG.lockInputWhileResponding) return;
        this.awaitingResponse = true;
        this.messageInput.disabled = true;
        this.messageInput.placeholder = 'Waiting for response...';
        this.messageInput.classList.add('input-locked');
        this.sendButton.disabled = true;
        this._disablePresetButtons();
    }

    _unlockInputAfterResponse() {
        if (!window.CONFIG.lockInputWhileResponding) return;
        if (!this.awaitingResponse) return;
        this.awaitingResponse = false;
        this.messageInput.placeholder = 'Type your message...';
        this.messageInput.classList.remove('input-locked');

        // Restore input state using the existing connection/auth gates.
        if (this.ws && this.ws.readyState === WebSocket.OPEN && (!window.CONFIG.requireAuth || this.authenticated)) {
            this.enableInput();
            // Return focus to the textarea so the user can keep typing without
            // having to click it again after the input is re-enabled.
            if (!this.messageInput.disabled) {
                this.messageInput.focus();
            }
        } else {
            this.messageInput.disabled = true;
            this.sendButton.disabled = true;
            this._disablePresetButtons();
        }
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
            if (this.awaitingResponse && window.CONFIG.lockInputWhileResponding) {
                btn.disabled = true;
                return;
            }
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

        // text: use validate field when present
        if (def?.validate === 'nonempty') return trimmed.length > 0;
        if (def?.validate) {
            try {
                return new RegExp(def.validate).test(trimmed);
            } catch (e) {
                console.warn(`Invalid validate pattern for variable "${varName}":`, e);
                return false;
            }
        }

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
        if (!this.activeConversationId) this._awaitingConversationId = true;
        this.ws.send(JSON.stringify({ type: 'chat', message: text }));
        this._lockInputForResponse();
    }

    // Parse ?prompt=<id>&VAR=value...&autosend=0 from the initial page URL.
    // Returns null when no `prompt` param is present. `autosend` defaults
    // to true (send immediately once variables validate); pass `autosend=0`
    // or `autosend=false` to only pre-fill the variable inputs instead.
    _parseDeepLink() {
        const params = new URLSearchParams(window.location.search);
        const promptId = params.get('prompt');
        if (!promptId) return null;

        const autosendParam = params.get('autosend');
        const values = {};
        params.forEach((value, key) => {
            if (key === 'prompt' || key === 'autosend') return;
            values[key] = value;
        });

        return {
            promptId,
            values,
            autosend: !(autosendParam === '0' || autosendParam === 'false'),
        };
    }

    // Apply a parsed deep link (see _parseDeepLink) once the chat is usable.
    // Fills any variable inputs matching querystring keys, then auto-sends
    // the matching preset prompt if all its required variables are valid
    // (unless autosend was explicitly disabled). Runs at most once per page
    // load — enableInput() can be called again (e.g. on reconnect) but the
    // deep link must not be re-applied/re-sent each time.
    _applyDeepLink() {
        if (this._deepLinkApplied || !this._deepLink) return;
        this._deepLinkApplied = true;

        const prompt = (window.CONFIG.presetPrompts || []).find(p => p.id === this._deepLink.promptId);
        if (!prompt) {
            console.warn(`Preset prompt deep link: unknown prompt id "${this._deepLink.promptId}"`);
            return;
        }

        Object.entries(this._deepLink.values).forEach(([name, value]) => {
            const el = document.getElementById(`var_${name}`);
            if (!el) return;
            const def = this._variableDefs[name];
            if (def && def.input_type === 'checkbox') {
                el.checked = value === 'true' || value === '1';
            } else {
                el.value = value;
            }
        });
        this._updatePresetButtonStates();

        if (!this._deepLink.autosend) return;

        const requiredVars = this._getPlaceholders(prompt.template || '');
        const allValid = requiredVars.every(name => this._validateVar(name, this._getVarValue(name)));
        if (allValid) {
            this._handlePresetClick(prompt);
            // Scrub the deep-link params from the address bar now that the
            // preset has actually been sent. Otherwise, if a subsequent
            // navigation happens (e.g. an SSO login redirect round trip that
            // lands back on this same URL via the preserved `next` param),
            // the reloaded page would parse the same ?prompt=...&VAR=...
            // querystring again and auto-send the exact same message a
            // second time as a duplicate, orphaned turn.
            this._clearDeepLinkFromUrl();
        } else {
            console.warn(
                'Preset prompt deep link: required variable(s) missing or invalid, not auto-sending',
                requiredVars
            );
        }
    }

    _clearDeepLinkFromUrl() {
        try {
            const url = new URL(window.location.href);
            url.searchParams.delete('prompt');
            url.searchParams.delete('autosend');
            Object.keys(this._deepLink.values).forEach(name => url.searchParams.delete(name));
            window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
        } catch (e) {
            console.warn('Could not clean up deep-link query string:', e);
        }
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
        // Respect response lock — prevent sending while awaiting a reply
        if (this.awaitingResponse && window.CONFIG.lockInputWhileResponding) {
            return;
        }

        // Auto-detect: run each variable's detect_pattern (or derive from validate)
        // against the sent message and populate the corresponding input if matched.
        for (const [name, def] of Object.entries(this._variableDefs)) {
            if (def.input_type && def.input_type !== 'text') continue;
            // Use explicit detect_pattern, or derive from validate by stripping anchors
            const pattern = def.detect_pattern
                || (def.validate && def.validate.replace(/^\^/, '').replace(/\$$/, ''))
                || null;
            if (!pattern) continue;
            let re;
            try {
                re = new RegExp(pattern, def.detect_flags || 'i');
            } catch (e) {
                console.warn(`Invalid detect pattern for variable "${name}":`, e);
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
        if (!this.activeConversationId) this._awaitingConversationId = true;
        this.ws.send(JSON.stringify({
            type: 'chat',
            message: message
        }));

        // Clear input and reset height
        this.messageInput.value = '';
        this.messageInput.style.height = 'auto';
        this.messageInput.style.height = '48px';  // Reset to min height
        this.messageInput.style.overflowY = 'hidden';

        // Lock input while waiting for the assistant response
        this._lockInputForResponse();
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
                this._updateConversationSidebarVisibility();
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
                this._updateConversationSidebarVisibility();
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
                this._updateConversationSidebarVisibility();
                break;

            case 'typing':
                this.showTypingIndicator(data.message || 'AI is typing...');
                break;

            case 'ai_response':
                this.hideTypingIndicator();
                this.addMessage('assistant', data.message, data.tool_calls, data.tool_results, data.message_id);
                this._unlockInputAfterResponse();
                if (data.conversation_id && data.conversation_id !== this.activeConversationId) {
                    this.activeConversationId = data.conversation_id;
                    this._renderConversationList();
                }
                this._awaitingConversationId = false;
                if (data.metadata && data.metadata.rejected_overrides && data.metadata.rejected_overrides.length) {
                    console.warn('Rejected config overrides:', data.metadata.rejected_overrides);
                }
                // Display the configured/effective model for this turn (accounts for
                // per-message overrides). Note: this may differ from the model that
                // actually produced the response if the server performed a
                // fallback-model retry; the server intentionally reports the
                // configured model here for client display, not the model that
                // actually answered. Prefer the human-readable name; fall back to the
                // raw model_id if an older server doesn't send model_name yet.
                if (this.modelIdDisplay && data.metadata && data.metadata.model_id) {
                    this.modelIdDisplay.textContent = data.metadata.model_name || data.metadata.model_id;
                }
                break;

            case 'conversation_list':
                this.conversations = data.conversations || [];
                this._renderConversationList();
                this._maybeRecoverPendingConversation();
                break;

            case 'conversation_created':
                this.activeConversationId = data.conversation_id;
                this._awaitingConversationId = false;
                this._upsertConversationInList({ id: data.conversation_id, title: null });
                break;

            case 'conversation_loaded':
                this.activeConversationId = data.conversation_id;
                this._awaitingConversationId = false;
                this._renderConversationHistory(data.messages || []);
                this._renderConversationList();
                this._closeSidebarDrawer();
                this._handleConversationPendingState(data.conversation_id, data.messages || []);
                break;

            case 'conversation_deleted':
                this.conversations = this.conversations.filter(c => c.id !== data.conversation_id);
                this._selectedConversationIds.delete(data.conversation_id);
                if (this.activeConversationId === data.conversation_id) {
                    this.activeConversationId = null;
                    this._clearChatArea();
                }
                this._renderConversationList();
                break;

            case 'conversation_bulk_deleted': {
                const deletedIds = new Set(data.deleted_ids || []);
                this.conversations = this.conversations.filter(c => !deletedIds.has(c.id));

                // Clear every id we *asked* to delete, not just the ones the
                // server reported deleting -- ids it skipped (already gone,
                // or not ours) would otherwise stay selected forever and keep
                // the bulk-delete bar showing a count that can never drop.
                const requestedIds = this._pendingBulkDeleteIds || deletedIds;
                requestedIds.forEach((id) => this._selectedConversationIds.delete(id));
                this._pendingBulkDeleteIds = null;

                // The server flag is authoritative, but fall back to our own
                // view in case this tab's active conversation drifted from
                // the session's (e.g. deleted from another tab).
                if (data.active_conversation_deleted || deletedIds.has(this.activeConversationId)) {
                    this.activeConversationId = null;
                    this._clearChatArea();
                }
                this._renderConversationList();

                // The sidebar only holds one page (conversation_list's
                // default limit), so deleting a page's worth can leave it
                // looking empty while older conversations still exist --
                // pull the next page in.
                if (deletedIds.size) this.requestConversationList();
                break;
            }

            case 'conversation_all_deleted':
                this.conversations = [];
                this._selectedConversationIds.clear();
                this.activeConversationId = null;
                this._clearChatArea();
                this._renderConversationList();
                break;

            case 'conversation_titled':
                this._upsertConversationInList({ id: data.conversation_id, title: data.title });
                break;

            case 'conversation_renamed':
                this._upsertConversationInList({ id: data.conversation_id, title: data.title });
                break;

            case 'conversation_error':
                console.warn('conversation_error', data);
                // Release the bulk-delete in-flight guard: the request was
                // rejected, so nothing was deleted and the user must be able
                // to retry.
                this._pendingBulkDeleteIds = null;
                if (data.code === 'conversation_history_unavailable') {
                    this.addMessage('system', `⚠️ ${data.message || 'This conversation\'s history is unavailable.'}`);
                }
                break;

            case 'feedback_ack':
                this._handleFeedbackAck(data);
                break;

            case 'feedback_error':
                this._handleFeedbackError(data);
                break;

            case 'config_updated':
                this._handleConfigUpdated(data);
                break;

            case 'error':
                this.hideTypingIndicator();
                this.addMessage('system', `Error: ${data.message}`);
                this._unlockInputAfterResponse();
                break;

            case 'auth_expired':
                // SSO session expired — prompt re-login
                this.authenticated = false;
                this.authPayload = null;
                this.authSent = false;
                this._unlockInputAfterResponse();
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
                this._updateConversationSidebarVisibility();
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

    addMessage(role, content, toolCalls, toolResults, messageId) {
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

        // Feedback controls: server-gated, assistant-only, requires message_id
        if (role === 'assistant'
            && window.CONFIG
            && window.CONFIG.feedbackEnabled === true
            && messageId) {
            const node = this._submittedFeedback.has(messageId)
                ? this._buildFeedbackSubmitted(messageId)
                : this._buildFeedbackControls(messageId);
            if (node) {
                messageDiv.appendChild(node);
            }
        }

        this.chatMessages.appendChild(messageDiv);
        this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
    }

    _buildFeedbackControls(messageId) {
        const wrapper = document.createElement('div');
        wrapper.className = 'feedback-controls';
        wrapper.dataset.messageId = messageId;

        const prompt = document.createElement('span');
        prompt.className = 'feedback-prompt';
        prompt.textContent = 'Was this response helpful?';
        wrapper.appendChild(prompt);

        const up = document.createElement('button');
        up.type = 'button';
        up.className = 'feedback-btn feedback-btn-up';
        up.dataset.messageId = messageId;
        up.dataset.rating = 'positive';
        up.setAttribute('aria-label', 'Rate response helpful');
        // Toggle semantics for assistive tech: starts unpressed and flips
        // to ``true`` on successful submission (handled in _handlePositiveClick
        // before the wrapper is replaced with the submitted indicator).
        up.setAttribute('aria-pressed', 'false');
        up.textContent = '👍';
        wrapper.appendChild(up);

        const down = document.createElement('button');
        down.type = 'button';
        down.className = 'feedback-btn feedback-btn-down';
        down.dataset.messageId = messageId;
        down.dataset.rating = 'negative';
        down.setAttribute('aria-label', 'Rate response unhelpful');
        down.setAttribute('aria-pressed', 'false');
        // aria-expanded reflects whether the correction form is currently
        // open. aria-controls is wired lazily when the form is built so its
        // id is referenced only when the element actually exists in the DOM.
        down.setAttribute('aria-expanded', 'false');
        down.textContent = '👎';
        wrapper.appendChild(down);

        // Event delegation: a single listener on the wrapper handles both
        // buttons. Thumbs-down is wired by T4; here T3 owns the positive
        // (one-click submit) path.
        wrapper.addEventListener('click', (event) => {
            const btn = event.target.closest('button.feedback-btn');
            if (!btn || !wrapper.contains(btn)) {
                return;
            }
            const rating = btn.dataset.rating;
            if (rating === 'positive') {
                this._handlePositiveClick(messageId, wrapper);
            } else if (rating === 'negative') {
                this._handleNegativeClick(messageId, wrapper, btn);
            }
        });

        return wrapper;
    }

    _handlePositiveClick(messageId, wrapper) {
        if (this._submittedFeedback.has(messageId)) {
            return; // idempotent
        }
        const upBtn = wrapper.querySelector('.feedback-btn-up');
        // Disable buttons immediately to prevent double-submit before the
        // round-trip completes.
        wrapper.querySelectorAll('button.feedback-btn').forEach((b) => {
            b.disabled = true;
        });
        // Optimistic ARIA state: the thumbs-up is now the active choice.
        if (upBtn) {
            upBtn.setAttribute('aria-pressed', 'true');
        }
        const ok = this._sendFeedback({ message_id: messageId, rating: 'positive' });
        if (!ok) {
            // Send failed locally (socket not open): re-enable and surface
            // an inline error rather than swap to the submitted state.
            wrapper.querySelectorAll('button.feedback-btn').forEach((b) => {
                b.disabled = false;
            });
            if (upBtn) {
                upBtn.setAttribute('aria-pressed', 'false');
            }
            this._showInlineFeedbackError(wrapper, 'Connection unavailable. Please try again.');
            return;
        }
        // Optimistic swap: mark locally and replace the controls with the
        // submitted indicator. On feedback_error we will revert.
        this._markFeedbackSubmitted(messageId);
        const submitted = this._buildFeedbackSubmitted(messageId);
        wrapper.replaceWith(submitted);
    }

    _handleNegativeClick(messageId, wrapper, downBtn) {
        if (this._submittedFeedback.has(messageId)) {
            return; // idempotent
        }
        // If a form is already open, this click is a no-op (textareas have
        // focus management of their own).
        if (wrapper.querySelector('.feedback-form')) {
            return;
        }
        downBtn.classList.add('selected');
        downBtn.setAttribute('aria-pressed', 'true');
        // Disable both buttons while the correction form is open so the user
        // cannot start a parallel positive submission mid-edit.
        wrapper.querySelectorAll('button.feedback-btn').forEach((b) => {
            b.disabled = true;
        });
        // Clear any stale inline error from a previous attempt.
        const staleErr = wrapper.querySelector('.feedback-error');
        if (staleErr) {
            staleErr.remove();
        }
        const form = this._buildCorrectionForm(messageId, wrapper, downBtn);
        // Wire aria-expanded / aria-controls now that the form exists in the
        // DOM and has a stable id.
        downBtn.setAttribute('aria-expanded', 'true');
        downBtn.setAttribute('aria-controls', form.id);
        wrapper.appendChild(form);
        const firstField = form.querySelector('textarea');
        if (firstField) {
            firstField.focus();
        }
    }

    _buildCorrectionForm(messageId, wrapper, downBtn) {
        const form = document.createElement('div');
        form.className = 'feedback-form';
        form.dataset.messageId = messageId;
        // Stable id so the parent ``feedback-btn-down`` can reference this
        // form via ``aria-controls``. ``messageId`` is server-generated and
        // safe to embed in an id.
        form.id = `feedback-form-${messageId}`;
        form.setAttribute('role', 'group');
        form.setAttribute('aria-label', 'Provide correction or comment');

        const correctionLabel = document.createElement('label');
        correctionLabel.className = 'feedback-form-label';
        correctionLabel.textContent = 'What should the correct answer be? (optional)';
        const correctionId = `feedback-correction-${messageId}`;
        correctionLabel.htmlFor = correctionId;
        const correctionField = document.createElement('textarea');
        correctionField.id = correctionId;
        correctionField.name = 'correction_text';
        correctionField.rows = 3;
        correctionField.className = 'feedback-textarea';
        form.appendChild(correctionLabel);
        form.appendChild(correctionField);

        const commentLabel = document.createElement('label');
        commentLabel.className = 'feedback-form-label';
        commentLabel.textContent = 'Additional comments (optional)';
        const commentId = `feedback-comment-${messageId}`;
        commentLabel.htmlFor = commentId;
        const commentField = document.createElement('textarea');
        commentField.id = commentId;
        commentField.name = 'user_comment';
        commentField.rows = 2;
        commentField.className = 'feedback-textarea';
        form.appendChild(commentLabel);
        form.appendChild(commentField);

        const actions = document.createElement('div');
        actions.className = 'feedback-form-actions';

        const cancelBtn = document.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.className = 'feedback-form-cancel';
        cancelBtn.textContent = 'Cancel';
        cancelBtn.addEventListener('click', () => {
            this._cancelCorrectionForm(wrapper, downBtn);
        });
        actions.appendChild(cancelBtn);

        const submitBtn = document.createElement('button');
        submitBtn.type = 'button';
        submitBtn.className = 'feedback-form-submit';
        submitBtn.textContent = 'Submit Feedback';
        submitBtn.addEventListener('click', () => {
            this._submitCorrectionForm(messageId, wrapper, form, submitBtn, cancelBtn);
        });
        actions.appendChild(submitBtn);

        form.appendChild(actions);
        return form;
    }

    _cancelCorrectionForm(wrapper, downBtn) {
        const form = wrapper.querySelector('.feedback-form');
        if (form) {
            form.remove();
        }
        if (downBtn) {
            downBtn.classList.remove('selected');
            downBtn.setAttribute('aria-pressed', 'false');
            downBtn.setAttribute('aria-expanded', 'false');
            downBtn.removeAttribute('aria-controls');
        }
        // Re-enable buttons; no message was sent.
        wrapper.querySelectorAll('button.feedback-btn').forEach((b) => {
            b.disabled = false;
        });
    }

    _submitCorrectionForm(messageId, wrapper, form, submitBtn, cancelBtn) {
        if (this._submittedFeedback.has(messageId)) {
            return; // idempotent
        }
        const correction = form.querySelector('textarea[name="correction_text"]').value.trim();
        const comment = form.querySelector('textarea[name="user_comment"]').value.trim();
        const payload = { message_id: messageId, rating: 'negative' };
        if (correction) {
            payload.correction_text = correction;
        }
        if (comment) {
            payload.user_comment = comment;
        }
        // Disable submit/cancel during round-trip.
        submitBtn.disabled = true;
        cancelBtn.disabled = true;
        const ok = this._sendFeedback(payload);
        if (!ok) {
            submitBtn.disabled = false;
            cancelBtn.disabled = false;
            this._showInlineFeedbackError(wrapper, 'Connection unavailable. Please try again.');
            return;
        }
        // Optimistic swap: replace the entire controls+form block with the
        // submitted indicator. feedback_error will revert (T3.3).
        this._markFeedbackSubmitted(messageId);
        const submitted = this._buildFeedbackSubmitted(messageId);
        wrapper.replaceWith(submitted);
    }

    _sendFeedback(payload) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            console.warn('Cannot send feedback: WebSocket not open');
            return false;
        }
        try {
            this.ws.send(JSON.stringify({ type: 'feedback', ...payload }));
            return true;
        } catch (err) {
            console.error('Failed to send feedback frame', err);
            return false;
        }
    }

    _buildFeedbackSubmitted(messageId) {
        const span = document.createElement('div');
        span.className = 'feedback-submitted';
        span.dataset.messageId = messageId;
        // ``status`` + ``aria-live=polite`` lets screen readers announce the
        // optimistic confirmation without stealing focus from the chat input.
        span.setAttribute('role', 'status');
        span.setAttribute('aria-live', 'polite');
        span.textContent = '✓ Feedback submitted';
        return span;
    }

    _handleFeedbackAck(data) {
        // Server confirmed persistence. The optimistic UI already shows
        // the submitted indicator, so this is a no-op besides logging.
        // Acks for unknown ids (e.g. after a tab refresh that wiped the
        // in-memory set but where sessionStorage was cleared too) are
        // benign and intentionally ignored.
        const messageId = data && data.message_id;
        if (messageId && !this._submittedFeedback.has(messageId)) {
            console.debug('feedback_ack for unknown message_id (ignored)', data);
            return;
        }
        console.debug('feedback_ack', data);
    }

    _handleFeedbackError(data) {
        const messageId = data && data.message_id;
        if (!messageId) {
            console.warn('feedback_error without message_id', data);
            return;
        }
        // Revert optimistic state so the user can retry.
        this._unmarkFeedbackSubmitted(messageId);
        const submitted = this.chatMessages.querySelector(
            `.feedback-submitted[data-message-id="${CSS.escape(messageId)}"]`
        );
        if (submitted) {
            const controls = this._buildFeedbackControls(messageId);
            const errorMsg = (data && data.message) || 'Could not submit feedback. Please try again.';
            this._showInlineFeedbackError(controls, errorMsg);
            submitted.replaceWith(controls);
        }
    }

    _showInlineFeedbackError(wrapper, message) {
        // Remove any existing error to avoid stacking.
        const existing = wrapper.querySelector('.feedback-error');
        if (existing) {
            existing.remove();
        }
        const err = document.createElement('span');
        err.className = 'feedback-error';
        err.setAttribute('role', 'alert');
        err.textContent = message;
        wrapper.appendChild(err);
    }

    _loadSubmittedFeedback() {
        // Best-effort: sessionStorage may be unavailable (private mode in
        // some browsers, sandboxed iframes). Degrade to in-memory Set.
        try {
            const raw = window.sessionStorage.getItem(this._feedbackStorageKey);
            if (!raw) {
                return new Set();
            }
            const parsed = JSON.parse(raw);
            if (Array.isArray(parsed)) {
                return new Set(parsed.filter((v) => typeof v === 'string'));
            }
        } catch (err) {
            console.warn('Failed to load submitted-feedback cache; using in-memory only', err);
        }
        return new Set();
    }

    _persistSubmittedFeedback() {
        try {
            window.sessionStorage.setItem(
                this._feedbackStorageKey,
                JSON.stringify(Array.from(this._submittedFeedback))
            );
        } catch (err) {
            // sessionStorage may throw (quota, private mode). Silently keep
            // the in-memory state authoritative.
            console.debug('sessionStorage write failed for submitted feedback', err);
        }
    }

    _markFeedbackSubmitted(messageId) {
        this._submittedFeedback.add(messageId);
        this._persistSubmittedFeedback();
    }

    _unmarkFeedbackSubmitted(messageId) {
        this._submittedFeedback.delete(messageId);
        this._persistSubmittedFeedback();
    }

    // ------------------------------------------------------------------
    // Conversation sidebar
    // ------------------------------------------------------------------

    _setupConversationSidebarListeners() {
        if (!this._conversationSidebarEnabled) return;
        if (this.newChatButton) {
            this.newChatButton.addEventListener('click', () => this.startNewConversation());
        }
        if (this.sidebarToggleButton) {
            this.sidebarToggleButton.addEventListener('click', () => this._toggleSidebarDrawer());
        }
        if (this.sidebarCloseButton) {
            this.sidebarCloseButton.addEventListener('click', () => this._closeSidebarDrawer());
        }
        if (this.sidebarBackdrop) {
            this.sidebarBackdrop.addEventListener('click', () => this._closeSidebarDrawer());
        }
        if (this.conversationSelectAllCheckbox) {
            this.conversationSelectAllCheckbox.addEventListener('change', (e) => {
                this._setAllConversationsSelected(e.target.checked);
            });
        }
        if (this.conversationBulkDeleteButton) {
            this.conversationBulkDeleteButton.addEventListener('click', () => this._bulkDeleteConversationsConfirm());
        }
    }

    _toggleSidebarDrawer() {
        if (!this.conversationSidebar) return;
        const opening = !this.conversationSidebar.classList.contains('open');
        this.conversationSidebar.classList.toggle('open', opening);
        if (this.sidebarBackdrop) this.sidebarBackdrop.classList.toggle('open', opening);
    }

    _closeSidebarDrawer() {
        if (this.conversationSidebar) this.conversationSidebar.classList.remove('open');
        if (this.sidebarBackdrop) this.sidebarBackdrop.classList.remove('open');
    }

    /**
     * Show/hide the sidebar based on config + auth state, and (re)populate it
     * once it becomes visible. Called on every auth state transition
     * (auth_configured, auth_failed, logout_success, auth_expired,
     * connection_established) so it stays correct across reconnects.
     */
    _updateConversationSidebarVisibility() {
        if (!this._conversationSidebarEnabled) return;
        const show = this.authenticated;
        if (this.conversationSidebar) this.conversationSidebar.classList.toggle('hidden', !show);
        if (this.sidebarToggleButton) this.sidebarToggleButton.classList.toggle('hidden', !show);

        if (!show) {
            this.activeConversationId = null;
            this._closeSidebarDrawer();
            return;
        }

        this.requestConversationList();
        // Auto-reconnect: the new connection has no active conversation on
        // the server (fresh session.metadata), so re-load the one this tab
        // was last showing rather than silently losing the association.
        if (this.activeConversationId) {
            this.loadConversation(this.activeConversationId, /* force */ true);
        }
    }

    // A conversation's last message is either a tool result awaiting the
    // next LLM call, or an assistant message that only issued tool_calls
    // (no final text yet) — in both cases the turn hasn't produced a final
    // answer. This happens when the connection that started the turn was
    // lost mid-flight (e.g. a network/proxy reconnect during a long
    // multi-round tool-calling loop): the graph invocation keeps running
    // server-side and checkpoints progressively, but there's no live
    // socket to deliver the eventual ai_response to.
    _isConversationPending(messages) {
        if (!messages || !messages.length) return false;
        const last = messages[messages.length - 1];
        if (last.role === 'tool') return true;
        if (last.role === 'assistant' && Array.isArray(last.tool_calls) && last.tool_calls.length > 0) return true;
        return false;
    }

    // Show a "still working" indicator and poll (by re-issuing
    // conversation_load) until the pending turn resolves, instead of
    // leaving the conversation looking silently stuck.
    //
    // Each poll re-requests conversation_load, whose conversation_loaded
    // reply routes right back through this same method — so the attempt
    // counter must only be reset when we *start* watching a (still) new
    // pending conversation, never on every intermediate reply, or
    // MAX_ATTEMPTS in _pollPendingConversation would never be reached.
    _handleConversationPendingState(conversationId, messages) {
        if (this._pendingPollTimer) {
            clearTimeout(this._pendingPollTimer);
            this._pendingPollTimer = null;
        }
        if (!this._isConversationPending(messages)) {
            this.hideTypingIndicator();
            this._pendingPollAttempts = 0;
            this._pendingConversationId = null;
            return;
        }
        this.showTypingIndicator('Still working on a previous request...');
        if (this._pendingConversationId !== conversationId) {
            this._pendingConversationId = conversationId;
            this._pendingPollAttempts = 0;
        }
        this._pollPendingConversation(conversationId);
    }

    _pollPendingConversation(conversationId) {
        const MAX_ATTEMPTS = 20; // ~2 minutes at 6s intervals
        const POLL_INTERVAL_MS = 6000;
        this._pendingPollTimer = setTimeout(() => {
            this._pendingPollAttempts += 1;
            // Stop polling if the user navigated elsewhere or the socket dropped.
            if (this.activeConversationId !== conversationId || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
                return;
            }
            if (this._pendingPollAttempts > MAX_ATTEMPTS) {
                this.hideTypingIndicator();
                return;
            }
            this.ws.send(JSON.stringify({ type: 'conversation_load', conversation_id: conversationId }));
        }, POLL_INTERVAL_MS);
    }

    requestConversationList() {
        if (!this._conversationSidebarEnabled || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
        this.ws.send(JSON.stringify({ type: 'conversation_list' }));
    }

    startNewConversation() {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
        this.ws.send(JSON.stringify({ type: 'conversation_new' }));
        this.activeConversationId = null;
        // Explicit user intent to start blank — don't let a later reconnect
        // auto-recover a stale pending turn back into view over this.
        this._awaitingConversationId = false;
        this._clearChatArea();
        this._renderConversationList();
        this._closeSidebarDrawer();
    }

    // If a first turn was sent but the connection dropped before its
    // conversation_id could be learned (see _awaitingConversationId), the
    // turn still completes and persists server-side — but this tab has no
    // way to know its id. Once a reconnect's conversation_list arrives with
    // nothing selected, auto-select the most-recently-updated conversation
    // so the recovered response is shown without the user having to
    // manually switch conversations to find it.
    _maybeRecoverPendingConversation() {
        if (!this._awaitingConversationId || this.activeConversationId || !this.conversations.length) return;
        this._awaitingConversationId = false;
        const mostRecent = [...this.conversations].sort((a, b) =>
            (b.updated_at || '').localeCompare(a.updated_at || '')
        )[0];
        if (mostRecent) {
            this.loadConversation(mostRecent.id, /* force */ true);
        }
    }

    loadConversation(conversationId, force = false) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
        if (!force && conversationId === this.activeConversationId) {
            this._closeSidebarDrawer();
            return;
        }
        this.ws.send(JSON.stringify({ type: 'conversation_load', conversation_id: conversationId }));
    }

    _clearChatArea() {
        this.chatMessages.innerHTML = '';
        const welcome = document.createElement('div');
        welcome.className = 'message system';
        const content = document.createElement('div');
        content.className = 'message-content';
        content.textContent = 'New conversation started.';
        welcome.appendChild(content);
        this.chatMessages.appendChild(welcome);
    }

    _renderConversationHistory(messages) {
        this.chatMessages.innerHTML = '';
        (messages || []).forEach((m) => {
            if (m.role === 'user' || m.role === 'assistant') {
                this.addMessage(m.role, m.content, m.tool_calls, m.tool_results, m.message_id);
            }
        });
        if (!messages || !messages.length) {
            this._clearChatArea();
        }
    }

    _upsertConversationInList(partial) {
        const now = new Date().toISOString();
        const idx = this.conversations.findIndex((c) => c.id === partial.id);
        if (idx === -1) {
            this.conversations.unshift(
                Object.assign({ id: partial.id, title: null, updated_at: now, message_count: 0 }, partial)
            );
        } else {
            this.conversations[idx] = Object.assign(
                {},
                this.conversations[idx],
                partial,
                { updated_at: partial.updated_at || now }
            );
        }
        this._renderConversationList();
    }

    _renameConversationPrompt(conv) {
        const next = window.prompt('Rename conversation', conv.title || '');
        if (next === null) return;
        const trimmed = next.trim();
        if (!trimmed || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
        this.ws.send(JSON.stringify({ type: 'conversation_rename', conversation_id: conv.id, title: trimmed }));
    }

    _deleteConversationConfirm(conv) {
        const label = conv.title || 'this conversation';
        if (!window.confirm(`Delete "${label}"? This cannot be undone.`)) return;
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
        this.ws.send(JSON.stringify({ type: 'conversation_delete', conversation_id: conv.id }));
    }

    // Toggle the options dropdown (Rename / Delete) for a conversation item,
    // anchored to the three-dot `anchorBtn` that triggered it. Portal-style
    // menu (appended to document.body, position: fixed) so it isn't clipped
    // by the scrollable `.conversation-list` -- same pattern as
    // _buildModelPicker's family/model flyouts.
    _optionsConversationDisplay(conv, anchorBtn) {
        // Clicking the same conversation's already-open menu closes it.
        const reopening = this._conversationOptionsConvId === conv.id;
        this._closeConversationOptionsMenu();
        if (reopening) return;

        const menu = document.createElement('ul');
        menu.className = 'conv-options-menu';
        menu.setAttribute('role', 'menu');

        const buildItem = (icon, label, onActivate, extraClass) => {
            const item = document.createElement('li');
            item.className = 'conv-options-menu-item' + (extraClass ? ` ${extraClass}` : '');
            item.setAttribute('role', 'menuitem');
            item.tabIndex = 0;

            const iconSpan = document.createElement('span');
            iconSpan.className = 'conv-options-menu-icon';
            iconSpan.textContent = icon;
            item.appendChild(iconSpan);

            const labelSpan = document.createElement('span');
            labelSpan.className = 'conv-options-menu-label';
            labelSpan.textContent = label;
            item.appendChild(labelSpan);

            const activate = (e) => {
                e.stopPropagation();
                this._closeConversationOptionsMenu();
                onActivate();
            };
            item.addEventListener('click', activate);
            item.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    activate(e);
                }
            });
            return item;
        };

        // Same icons used elsewhere for these actions (✎ rename, 🗑 delete).
        menu.appendChild(buildItem('✎', 'Rename', () => this._renameConversationPrompt(conv)));
        menu.appendChild(buildItem('🗑', 'Delete', () => this._deleteConversationConfirm(conv), 'conv-options-menu-item-danger'));

        document.body.appendChild(menu);
        this._conversationOptionsMenuEl = menu;
        this._conversationOptionsConvId = conv.id;
        this._conversationOptionsAnchorEl = anchorBtn;

        const rect = anchorBtn.getBoundingClientRect();
        menu.style.top = `${rect.bottom + 4}px`;
        menu.style.right = `${window.innerWidth - rect.right}px`;

        anchorBtn.setAttribute('aria-expanded', 'true');
        this._bindConversationOptionsGlobalListeners();
    }

    _closeConversationOptionsMenu() {
        if (this._conversationOptionsAnchorEl) {
            this._conversationOptionsAnchorEl.setAttribute('aria-expanded', 'false');
        }
        if (this._conversationOptionsMenuEl) {
            this._conversationOptionsMenuEl.remove();
        }
        this._conversationOptionsMenuEl = null;
        this._conversationOptionsConvId = null;
        this._conversationOptionsAnchorEl = null;
    }

    _bindConversationOptionsGlobalListeners() {
        if (this._conversationOptionsGlobalListenersBound) return;
        this._conversationOptionsGlobalListenersBound = true;
        document.addEventListener('click', (event) => {
            if (!this._conversationOptionsMenuEl) return;
            if (this._conversationOptionsMenuEl.contains(event.target)
                || (this._conversationOptionsAnchorEl && this._conversationOptionsAnchorEl.contains(event.target))) {
                return;
            }
            this._closeConversationOptionsMenu();
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') this._closeConversationOptionsMenu();
        });
        // Portal menu is position: fixed against the viewport, not the
        // scrollable list -- close it on scroll rather than let it drift away
        // from its anchor.
        if (this.conversationList) {
            this.conversationList.addEventListener('scroll', () => this._closeConversationOptionsMenu());
        }
    }

    _renderConversationList() {
        if (!this.conversationList) return;
        this.conversationList.innerHTML = '';

        // The server already orders newest-updated-first; re-sort
        // defensively since client-side upserts (title/rename) can
        // perturb ordering without a fresh conversation_list round-trip.
        const sorted = [...this.conversations].sort((a, b) =>
            (b.updated_at || '').localeCompare(a.updated_at || '')
        );

        // Drop selections for conversations that no longer exist (deleted
        // elsewhere, e.g. via the single-item delete menu) so the bulk-delete
        // bar's count and the select-all checkbox stay accurate.
        const liveIds = new Set(sorted.map((c) => c.id));
        for (const id of [...this._selectedConversationIds]) {
            if (!liveIds.has(id)) this._selectedConversationIds.delete(id);
        }

        sorted.forEach((conv) => {
            const li = document.createElement('li');
            li.className = 'conversation-item' + (conv.id === this.activeConversationId ? ' active' : '');
            li.dataset.conversationId = conv.id;

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'conversation-item-checkbox';
            checkbox.setAttribute('aria-label', `Select ${conv.title || 'conversation'}`);
            checkbox.checked = this._selectedConversationIds.has(conv.id);
            checkbox.addEventListener('click', (e) => e.stopPropagation());
            checkbox.addEventListener('change', (e) => {
                this._toggleConversationSelection(conv.id, e.target.checked);
            });
            li.appendChild(checkbox);

            const titleSpan = document.createElement('span');
            titleSpan.className = 'conversation-item-title';
            titleSpan.textContent = conv.title || 'Untitled conversation';
            li.appendChild(titleSpan);

            const actions = document.createElement('span');
            actions.className = 'conversation-item-actions';

            const optionsBtn = document.createElement('button');
            optionsBtn.type = 'button';
            optionsBtn.className = 'conv-action-btn conv-option-btn';
            optionsBtn.title = 'Options';
            optionsBtn.setAttribute('aria-label', 'Options for conversation');
            optionsBtn.setAttribute('aria-haspopup', 'true');
            optionsBtn.setAttribute('aria-expanded', 'false');
            optionsBtn.textContent = '⋮';
            optionsBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._optionsConversationDisplay(conv, optionsBtn);
            });
            actions.appendChild(optionsBtn);

            li.appendChild(actions);
            li.setAttribute('role', 'button');
            li.tabIndex = 0;
            li.addEventListener('click', () => this.loadConversation(conv.id));
            li.addEventListener('keydown', (e) => {
                if (e.target !== li) return; // ignore keypresses on nested action buttons
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    this.loadConversation(conv.id);
                }
            });
            this.conversationList.appendChild(li);
        });

        this._updateBulkDeleteBarState();
    }

    // ------------------------------------------------------------------
    // Multi-select + bulk deletion
    // ------------------------------------------------------------------

    _toggleConversationSelection(conversationId, selected) {
        if (selected) {
            this._selectedConversationIds.add(conversationId);
        } else {
            this._selectedConversationIds.delete(conversationId);
        }
        this._updateBulkDeleteBarState();
    }

    _setAllConversationsSelected(selected) {
        if (selected) {
            this.conversations.forEach((c) => this._selectedConversationIds.add(c.id));
        } else {
            this._selectedConversationIds.clear();
        }
        this._renderConversationList();
    }

    // Keeps the "Select all" checkbox, the checked state of each rendered
    // checkbox, and the bulk-delete action bar (count + visibility) in sync
    // with `_selectedConversationIds` — called after every render and every
    // individual selection toggle.
    _updateBulkDeleteBarState() {
        const total = this.conversations.length;
        const selectedCount = this._selectedConversationIds.size;

        if (this.conversationSelectAllCheckbox) {
            this.conversationSelectAllCheckbox.checked = total > 0 && selectedCount === total;
            this.conversationSelectAllCheckbox.indeterminate = selectedCount > 0 && selectedCount < total;
        }

        if (this.conversationBulkDeleteBar) {
            this.conversationBulkDeleteBar.classList.toggle('hidden', selectedCount === 0);
        }
        if (this.conversationBulkDeleteCount) {
            this.conversationBulkDeleteCount.textContent =
                `${selectedCount} selected`;
        }
    }

    _bulkDeleteConversationsConfirm() {
        if (this._pendingBulkDeleteIds) return; // a bulk delete is already in flight
        const ids = [...this._selectedConversationIds];
        if (!ids.length) return;
        const label = ids.length === 1 ? '1 conversation' : `${ids.length} conversations`;
        if (!window.confirm(`Delete ${label}? This cannot be undone.`)) return;
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            // The user confirmed a destructive action -- don't fail silently.
            this.addMessage('system', '⚠️ Not connected — conversations were not deleted.');
            return;
        }
        this._pendingBulkDeleteIds = new Set(ids);
        this.ws.send(JSON.stringify({ type: 'conversation_delete_bulk', conversation_ids: ids }));
    }

    // ------------------------------------------------------------------
    // Dynamic parameter overrides settings sidebar
    // ------------------------------------------------------------------

    _setupConfigSidebarListeners() {
        if (!this._configSidebarEnabled) return;
        if (this.configSidebarToggleButton) {
            this.configSidebarToggleButton.addEventListener('click', () => this._toggleConfigSidebarDrawer());
        }
        if (this.configSidebarCloseButton) {
            this.configSidebarCloseButton.addEventListener('click', () => this._closeConfigSidebarDrawer());
        }
        if (this.configSidebarBackdrop) {
            this.configSidebarBackdrop.addEventListener('click', () => this._closeConfigSidebarDrawer());
        }
        if (this.configResetButton) {
            this.configResetButton.addEventListener('click', () => this._sendConfigReset());
        }
        this._renderConfigSidebarControls();
    }

    _toggleConfigSidebarDrawer() {
        if (!this.configSidebar) return;
        const opening = !this.configSidebar.classList.contains('open');
        this.configSidebar.classList.toggle('open', opening);
        if (this.configSidebarBackdrop) this.configSidebarBackdrop.classList.toggle('open', opening);
    }

    _closeConfigSidebarDrawer() {
        if (this.configSidebar) this.configSidebar.classList.remove('open');
        if (this.configSidebarBackdrop) this.configSidebarBackdrop.classList.remove('open');
    }

    /** Definitions for every dynamically-overridable parameter's sidebar control. */
    static get DYNAMIC_OVERRIDE_FIELDS() {
        return [
            {
                key: 'model_id', label: 'Model', type: 'select',
                // Sourced from AUTOCHAT_AVAILABLE_MODELS (server config), not hardcoded --
                // see window.CONFIG.availableModels (set in plugin.py / chat.html).
                options: window.CONFIG.availableModels || [],
            },
            { key: 'temperature', label: 'Temperature', type: 'range', min: 0, max: 1, step: 0.1 },
            { key: 'top_p', label: 'Top P', type: 'range', min: 0, max: 1, step: 0.1 },
            { key: 'max_tokens', label: 'Max output tokens', type: 'number', min: 1, max: 100000, step: 1 },
            {
                key: 'enable_ai_summarization', label: 'AI summarization', type: 'checkbox',
                tooltip: 'Technique for handling input that\'s too large to process in one go. '
                    + 'Off: the text is smartly truncated — faster responses, less token usage. '
                    + 'On: everything is processed in chunks instead — no info is lost, but token usage '
                    + 'is much higher. Has no effect when the input isn\'t actually large.',
            },
            { key: 'enable_rag', label: 'Knowledge base (RAG)', type: 'checkbox' },
            { key: 'kb_top_k_results', label: 'KB results (top-K)', type: 'number', min: 1, max: 20, step: 1 },
            {
                key: 'kb_similarity_threshold', label: 'KB similarity threshold', type: 'range', min: 0, max: 1, step: 0.05,
                tooltip: 'Minimum relevance score a knowledge-base result must have to be used '
                    + '(0.0–1.0). Recommended range: 0.3–0.7. Lower values return more results but some '
                    + 'may be less relevant; higher values are stricter and may return nothing if no good match exists.',
            },
        ];
    }

    /** Only the fields allowed by the server's `allowedDynamicOverrides` (null = all). */
    _visibleOverrideFields() {
        const fields = ChatClient.DYNAMIC_OVERRIDE_FIELDS;
        if (!this._allowedDynamicOverrides) return fields;
        const allowed = new Set(this._allowedDynamicOverrides);
        return fields.filter((f) => allowed.has(f.key));
    }

    /** Human-readable display name for a model_id, sourced from
     * window.CONFIG.availableModels ({id, name} entries -- see
     * ChatConfig.get_available_models_for_ui()). Falls back to the raw id
     * itself if it isn't found there (e.g. a fallback_model not offered in
     * the dropdown). */
    _modelIdToName(modelId) {
        if (!modelId) return modelId;
        const match = (window.CONFIG.availableModels || []).find((m) => m.id === modelId);
        return match ? match.name : modelId;
    }

    /** Loose equality for comparing an override's current value against its
     * global default, used to decide whether a control should show the
     * "overridden" highlight. Handles booleans (checkbox values arrive as
     * real booleans) and numbers (floating-point tolerance for the range
     * sliders, since e.g. 0.1 + 0.2 !== 0.3 in JS) alongside plain string
     * equality for model_id. */
    _valueEqualsDefault(value, defaultValue) {
        if (typeof value === 'boolean' || typeof defaultValue === 'boolean') {
            return !!value === !!defaultValue;
        }
        if (typeof value === 'number' && typeof defaultValue === 'number') {
            return Math.abs(value - defaultValue) < 1e-9;
        }
        return value === defaultValue;
    }

    /** Build a small "?" icon that shows `tooltipText` on hover/focus (see the
     * .config-help-icon CSS rules for the actual tooltip bubble). Focusable
     * and labeled for keyboard/screen-reader users, not just mouse hover. */
    _buildHelpIcon(tooltipText) {
        const icon = document.createElement('span');
        icon.className = 'config-help-icon';
        icon.textContent = '?';
        icon.dataset.tooltip = tooltipText;
        icon.title = tooltipText;
        icon.tabIndex = 0;
        icon.setAttribute('role', 'img');
        icon.setAttribute('aria-label', tooltipText);
        return icon;
    }

    _renderConfigSidebarControls() {
        if (!this._configSidebarEnabled || !this.configSidebarBody) return;
        this.configSidebarBody.innerHTML = '';

        this._visibleOverrideFields().forEach((field) => {
            const row = document.createElement('div');
            row.className = 'config-control-row';
            row.dataset.overrideKey = field.key;

            if (field.type === 'checkbox') {
                row.classList.add('config-toggle-row');
                const label = document.createElement('label');
                label.htmlFor = `override_${field.key}`;
                label.appendChild(document.createTextNode(field.label));
                if (field.tooltip) {
                    label.appendChild(this._buildHelpIcon(field.tooltip));
                }
                const input = document.createElement('input');
                input.type = 'checkbox';
                input.id = `override_${field.key}`;
                input.checked = !!(window.CONFIG.overrideDefaults || {})[field.key];
                if (field.key === 'enable_rag') {
                    input.addEventListener('change', () => {
                        this._sendConfigUpdate(field.key, input.checked);
                        this._updateKbControlsVisibility(input.checked);
                    });
                } else {
                    input.addEventListener('change', () => this._sendConfigUpdate(field.key, input.checked));
                }
                row.appendChild(label);
                row.appendChild(input);
                this.configSidebarBody.appendChild(row);
                return;
            }

            const label = document.createElement('label');
            label.htmlFor = `override_${field.key}`;
            const labelTextWrap = document.createElement('span');
            labelTextWrap.className = 'config-label-text';
            labelTextWrap.appendChild(document.createTextNode(field.label));
            if (field.tooltip) {
                labelTextWrap.appendChild(this._buildHelpIcon(field.tooltip));
            }
            label.appendChild(labelTextWrap);
            const valueSpan = document.createElement('span');
            valueSpan.className = 'config-control-value';
            label.appendChild(valueSpan);
            row.appendChild(label);

            if (field.type === 'select') {
                // Family -> model picker: a single trigger button that opens a
                // flyout menu. The menu lists providers (families); hovering
                // (or tapping) a family opens a SECOND menu docked to its side
                // listing that family's models, instead of dumping every model
                // from every provider into one giant flat list/dropdown.
                row.appendChild(this._buildModelPicker(field));
                this.configSidebarBody.appendChild(row);
                return;
            }

            let input;
            if (field.type === 'range') {
                input = document.createElement('input');
                input.type = 'range';
                input.min = field.min;
                input.max = field.max;
                input.step = field.step;
                input.value = (window.CONFIG.overrideDefaults || {})[field.key] ?? field.min;
                valueSpan.textContent = input.value;
                input.addEventListener('input', () => { valueSpan.textContent = input.value; });
                input.addEventListener('change', () => this._sendConfigUpdate(field.key, parseFloat(input.value)));
            } else {
                input = document.createElement('input');
                input.type = 'number';
                input.min = field.min;
                input.max = field.max;
                input.step = field.step;
                input.value = (window.CONFIG.overrideDefaults || {})[field.key] ?? field.min;
                if (field.key === 'max_tokens') {
                    // Clamp to the input's current `max` (kept in sync with the
                    // selected model's max_output_tokens by _updateMaxTokensCapForModel)
                    // in case the user types a value exceeding it directly.
                    input.addEventListener('change', () => {
                        const cap = parseInt(input.max, 10);
                        let value = parseInt(input.value, 10) || field.min;
                        if (!Number.isNaN(cap) && value > cap) value = cap;
                        input.value = value;
                        this._sendConfigUpdate(field.key, value);
                    });
                } else {
                    input.addEventListener('change', () => this._sendConfigUpdate(field.key, parseInt(input.value, 10)));
                }
            }
            input.id = `override_${field.key}`;
            row.appendChild(input);
            this.configSidebarBody.appendChild(row);
        });

        // Initial visibility of the temperature/top_p rows, and the max_tokens
        // cap, must match whichever model is currently selected (default or an
        // already-active override).
        const initialModelId = (window.CONFIG.overrideDefaults || {}).model_id || window.CONFIG.modelId;
        this._updateTemperatureControlsVisibility(initialModelId);
        this._updateMaxTokensCapForModel(initialModelId);

        // Same idea for the KB controls: only relevant when RAG is enabled.
        const initialEnableRag = !!(window.CONFIG.overrideDefaults || {}).enable_rag;
        this._updateKbControlsVisibility(initialEnableRag);
    }

    /** Show/hide the kb_top_k_results and kb_similarity_threshold sidebar rows
     * together, based on whether RAG (`enable_rag`) is currently on -- those
     * controls are meaningless when RAG is disabled. */
    _updateKbControlsVisibility(enableRag) {
        if (!this.configSidebarBody) return;
        ['kb_top_k_results', 'kb_similarity_threshold'].forEach((key) => {
            const row = this.configSidebarBody.querySelector(`.config-control-row[data-override-key="${key}"]`);
            if (row) row.classList.toggle('hidden', !enableRag);
        });
    }

    /** Show/hide the temperature and top_p sidebar rows together, based on
     * whether `modelId` supports temperature sampling
     * (`_PROFILES[modelId]["temperature"]`, via ChatConfig.get_available_models_for_ui()).
     * top_p is gated on the same flag as temperature (there's no separate top_p
     * flag in _PROFILES, and Bedrock Converse only accepts one of the two per
     * request anyway -- see _build_llm in graph/nodes/llm_call.py) -- models
     * that disable temperature typically don't support top_p either. */
    _updateTemperatureControlsVisibility(modelId) {
        if (!this.configSidebarBody) return;
        const model = (window.CONFIG.availableModels || []).find((m) => m.id === modelId);
        const supportsTemperature = model ? model.supports_temperature !== false : true;
        ['temperature', 'top_p'].forEach((key) => {
            const row = this.configSidebarBody.querySelector(`.config-control-row[data-override-key="${key}"]`);
            if (row) row.classList.toggle('hidden', !supportsTemperature);
        });
    }

    /** Cap the max_tokens control's upper bound to `modelId`'s
     * `_PROFILES[modelId]["max_output_tokens"]` (via
     * ChatConfig.get_available_models_for_ui()), and clamp its current value
     * down (sending the clamped value as a config_update) if it now exceeds
     * that cap -- e.g. after switching to a model with a smaller output limit,
     * or after a reset whose global-default max_tokens exceeds the model's cap.
     * Falls back to the control's static ceiling (DYNAMIC_OVERRIDE_FIELDS'
     * max_tokens.max) when the model's max_output_tokens isn't known. */
    _updateMaxTokensCapForModel(modelId) {
        if (!this.configSidebarBody) return;
        const row = this.configSidebarBody.querySelector('.config-control-row[data-override-key="max_tokens"]');
        if (!row) return;
        const input = row.querySelector('input[type="number"]');
        if (!input) return;

        const model = (window.CONFIG.availableModels || []).find((m) => m.id === modelId);
        const field = ChatClient.DYNAMIC_OVERRIDE_FIELDS.find((f) => f.key === 'max_tokens');
        const cap = (model && model.max_output_tokens) || (field && field.max);
        if (!cap) return;

        input.max = cap;
        const currentValue = parseInt(input.value, 10);
        if (!Number.isNaN(currentValue) && currentValue > cap) {
            input.value = cap;
            const valueSpan = row.querySelector('.config-control-value');
            if (valueSpan) valueSpan.textContent = cap;
            this._sendConfigUpdate('max_tokens', cap);
        }
    }

    /** Return provider groups from the current server, with a flat-list
     * fallback for compatibility with older template contexts. */
    _getAvailableModelGroups(flatOptions) {
        const configuredGroups = window.CONFIG.availableModelGroups || [];
        if (configuredGroups.length) return configuredGroups;

        const groups = new Map();
        (flatOptions || []).forEach((model) => {
            const provider = model.provider || 'Models';
            if (!groups.has(provider)) groups.set(provider, []);
            groups.get(provider).push(model);
        });
        return Array.from(groups, ([provider, models]) => ({ provider, models }));
    }

    /** Build the family -> model picker: a single trigger button plus two
     * portal menus (appended to `document.body`, not the row itself, so they
     * aren't clipped by the scrollable `.config-sidebar-body`):
     *   - `this._modelMenuEl` -- the family ("Anthropic", "OpenAI", "xAI", ...)
     *     list, opened by clicking the trigger.
     *   - `this._modelFlyoutEl` -- a SECOND menu docked to the side of
     *     whichever family item is hovered/tapped, listing that family's
     *     models. Only one flyout element is reused across families.
     * Both are `position: fixed` and positioned from `getBoundingClientRect()`
     * of their anchor each time they open, so they track the trigger/family
     * item regardless of where the sidebar is scrolled to. */
    _buildModelPicker(field) {
        const currentModel = (window.CONFIG.overrideDefaults || {}).model_id || window.CONFIG.modelId;
        const groups = this._getAvailableModelGroups(field.options);

        // Re-rendering the sidebar (shouldn't normally happen more than once,
        // but stay idempotent) must not leave stale portal elements behind.
        if (this._modelMenuEl) this._modelMenuEl.remove();
        if (this._modelFlyoutEl) this._modelFlyoutEl.remove();

        const wrapper = document.createElement('div');
        wrapper.className = 'config-model-picker';

        const trigger = document.createElement('button');
        trigger.type = 'button';
        trigger.id = `override_${field.key}`;
        trigger.className = 'config-model-trigger';
        trigger.setAttribute('aria-haspopup', 'true');
        trigger.setAttribute('aria-expanded', 'false');
        const triggerLabel = document.createElement('span');
        triggerLabel.className = 'config-model-trigger-label';
        triggerLabel.textContent = this._modelIdToName(currentModel);
        const triggerCaret = document.createElement('span');
        triggerCaret.className = 'config-model-trigger-caret';
        triggerCaret.textContent = '\u25BE';
        trigger.appendChild(triggerLabel);
        trigger.appendChild(triggerCaret);
        wrapper.appendChild(trigger);

        const menu = document.createElement('div');
        menu.className = 'config-model-menu';
        menu.hidden = true;
        const familyList = document.createElement('ul');
        familyList.className = 'config-model-family-list';
        familyList.setAttribute('role', 'menu');
        menu.appendChild(familyList);

        const flyout = document.createElement('ul');
        flyout.className = 'config-model-flyout';
        flyout.setAttribute('role', 'menu');
        flyout.hidden = true;

        document.body.appendChild(menu);
        document.body.appendChild(flyout);
        this._modelMenuEl = menu;
        this._modelFlyoutEl = flyout;
        this._modelTriggerEl = trigger;
        this._modelTriggerLabelEl = triggerLabel;

        const cancelHideFlyout = () => {
            if (this._modelFlyoutHideTimer) {
                clearTimeout(this._modelFlyoutHideTimer);
                this._modelFlyoutHideTimer = null;
            }
        };
        const scheduleHideFlyout = () => {
            cancelHideFlyout();
            this._modelFlyoutHideTimer = setTimeout(() => { flyout.hidden = true; }, 200);
        };
        flyout.addEventListener('mouseenter', cancelHideFlyout);
        flyout.addEventListener('mouseleave', scheduleHideFlyout);

        const selectModel = (modelId, modelName) => {
            triggerLabel.textContent = modelName;
            this._sendConfigUpdate(field.key, modelId);
            this._updateTemperatureControlsVisibility(modelId);
            this._updateMaxTokensCapForModel(modelId);
            this._closeModelMenu();
        };

        const openFlyoutForFamily = (familyItem, group) => {
            cancelHideFlyout();
            familyList.querySelectorAll('.config-model-family-item').forEach((el) => {
                el.classList.toggle('open', el === familyItem);
            });

            flyout.innerHTML = '';
            (group.models || []).forEach((model) => {
                const option = document.createElement('li');
                option.className = 'config-model-option';
                option.setAttribute('role', 'menuitem');
                option.tabIndex = 0;
                option.dataset.modelId = model.id;
                option.textContent = model.name;
                if (model.id === currentModel) option.classList.add('selected');
                option.addEventListener('click', (event) => {
                    event.stopPropagation();
                    selectModel(model.id, model.name);
                });
                option.addEventListener('keydown', (event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        event.stopPropagation();
                        selectModel(model.id, model.name);
                    }
                });
                flyout.appendChild(option);
            });

            // Dock the flyout to the SIDE of the family menu, at the same
            // height as the hovered/tapped family row (`right` rather than
            // `left`, since the config sidebar -- and this menu -- sit at the
            // right edge of the viewport, so the flyout must open leftward to
            // stay on-screen).
            const menuRect = menu.getBoundingClientRect();
            const itemRect = familyItem.getBoundingClientRect();
            flyout.style.top = `${itemRect.top}px`;
            flyout.style.right = `${window.innerWidth - menuRect.left + 4}px`;
            flyout.hidden = false;
        };

        familyList.innerHTML = '';
        groups.forEach((group) => {
            const familyItem = document.createElement('li');
            familyItem.className = 'config-model-family-item';
            familyItem.setAttribute('role', 'menuitem');
            familyItem.setAttribute('aria-haspopup', 'true');
            familyItem.tabIndex = 0;
            familyItem.dataset.provider = group.provider;
            if ((group.models || []).some((model) => model.id === currentModel)) {
                familyItem.classList.add('current-family');
            }
            const name = document.createElement('span');
            name.className = 'config-model-family-name';
            name.textContent = group.provider;
            const arrow = document.createElement('span');
            arrow.className = 'config-model-family-arrow';
            arrow.textContent = '\u25B8';
            familyItem.appendChild(name);
            familyItem.appendChild(arrow);
            // Hover opens the side flyout (desktop mouse UX); click/tap does
            // the same for touch, and Enter/Space/ArrowRight below covers
            // keyboard users -- neither of whom can "hover".
            familyItem.addEventListener('mouseenter', () => openFlyoutForFamily(familyItem, group));
            familyItem.addEventListener('click', (event) => {
                event.stopPropagation();
                openFlyoutForFamily(familyItem, group);
            });
            // Enter/Space opens the flyout; ArrowRight does the same and moves
            // focus straight onto the first model so the list is reachable
            // without a pointer.
            familyItem.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ' || event.key === 'ArrowRight') {
                    event.preventDefault();
                    event.stopPropagation();
                    openFlyoutForFamily(familyItem, group);
                    const firstOption = flyout.querySelector('.config-model-option');
                    if (firstOption) firstOption.focus();
                }
            });
            familyList.appendChild(familyItem);
        });

        trigger.addEventListener('click', (event) => {
            event.stopPropagation();
            if (!menu.hidden) {
                this._closeModelMenu();
                return;
            }
            const rect = trigger.getBoundingClientRect();
            menu.style.top = `${rect.bottom + 4}px`;
            menu.style.right = `${window.innerWidth - rect.right}px`;
            menu.style.minWidth = `${rect.width}px`;
            menu.hidden = false;
            trigger.setAttribute('aria-expanded', 'true');

            // Open straight to the current model's family flyout so it's
            // visible without the user having to hunt for it first.
            const currentFamilyItem = familyList.querySelector('.config-model-family-item.current-family');
            const currentGroup = groups.find((group) => group.provider === (currentFamilyItem && currentFamilyItem.dataset.provider));
            if (currentFamilyItem && currentGroup) {
                openFlyoutForFamily(currentFamilyItem, currentGroup);
            }
        });

        if (!this._modelMenuGlobalListenersBound) {
            this._modelMenuGlobalListenersBound = true;
            document.addEventListener('click', (event) => {
                if (!this._modelMenuEl || this._modelMenuEl.hidden) return;
                const target = event.target;
                if (this._modelMenuEl.contains(target) || this._modelFlyoutEl.contains(target)
                    || (this._modelTriggerEl && this._modelTriggerEl.contains(target))) {
                    return;
                }
                this._closeModelMenu();
            });
            document.addEventListener('keydown', (event) => {
                if (event.key === 'Escape') this._closeModelMenu();
            });
        }
        // The menus are `position: fixed` against the viewport, not the
        // scrollable sidebar body -- close them on scroll rather than let
        // them drift away from their anchor.
        this.configSidebarBody.addEventListener('scroll', () => this._closeModelMenu());

        return wrapper;
    }

    /** Close the family menu and its side model flyout, if open. */
    _closeModelMenu() {
        if (this._modelMenuEl) {
            this._modelMenuEl.hidden = true;
            this._modelMenuEl.querySelectorAll('.config-model-family-item.open').forEach((el) => el.classList.remove('open'));
        }
        if (this._modelFlyoutEl) this._modelFlyoutEl.hidden = true;
        if (this._modelTriggerEl) this._modelTriggerEl.setAttribute('aria-expanded', 'false');
    }

    /** Keep the trigger label and family highlight aligned with a
     * server-confirmed model override or reset. */
    _syncModelSelectors(modelId) {
        if (!modelId) return;
        if (this._modelTriggerLabelEl) {
            this._modelTriggerLabelEl.textContent = this._modelIdToName(modelId);
        }
        if (this._modelMenuEl) {
            const groups = this._getAvailableModelGroups(window.CONFIG.availableModels || []);
            this._modelMenuEl.querySelectorAll('.config-model-family-item').forEach((el) => {
                const group = groups.find((candidate) => candidate.provider === el.dataset.provider);
                const isCurrentFamily = !!group && (group.models || []).some((model) => model.id === modelId);
                el.classList.toggle('current-family', isCurrentFamily);
            });
        }
        if (this._modelFlyoutEl && !this._modelFlyoutEl.hidden) {
            this._modelFlyoutEl.querySelectorAll('.config-model-option').forEach((el) => {
                el.classList.toggle('selected', el.dataset.modelId === modelId);
            });
        }
    }

    _sendConfigUpdate(key, value) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
        this.ws.send(JSON.stringify({
            type: 'config_update',
            config_overrides: { [key]: value },
            override_mode: 'session',
        }));
    }

    _sendConfigReset() {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
        this.ws.send(JSON.stringify({ type: 'config_reset' }));
    }

    /** Handle the server's `config_updated` confirmation: refresh the badge
     * count and highlight which controls currently have an active override. */
    _handleConfigUpdated(data) {
        this._activeConfigOverrides = data.active_overrides || {};

        if (data.rejected_overrides && data.rejected_overrides.length) {
            this.addMessage('system', `⚠️ Some settings were rejected: ${data.rejected_overrides.join('; ')}`);
        }

        if (this.configOverrideBadge) {
            // Count only settings whose value actually differs from the global
            // default: `active_overrides` can still carry a key whose value
            // equals the default (the user moved a control and then put it
            // back without sending `config_reset`), so a raw key count would
            // overstate how many settings are actually customised.
            const defaults = window.CONFIG.overrideDefaults || {};
            const count = Object.entries(this._activeConfigOverrides).filter(
                ([key, value]) => !this._valueEqualsDefault(value, defaults[key]),
            ).length;
            this.configOverrideBadge.textContent = String(count);
            this.configOverrideBadge.classList.toggle('hidden', count === 0);
        }

        if (this.modelIdDisplay) {
            const overriddenModelId = this._activeConfigOverrides.model_id;
            const defaultModelId = (window.CONFIG.overrideDefaults || {}).model_id || window.CONFIG.modelId;
            this.modelIdDisplay.textContent = this._modelIdToName(overriddenModelId || defaultModelId);
        }

        if (this.configSidebarBody) {
            this.configSidebarBody.querySelectorAll('.config-control-row').forEach((row) => {
                const key = row.dataset.overrideKey;
                const isOverridden = Object.prototype.hasOwnProperty.call(this._activeConfigOverrides, key);
                const defaultValue = (window.CONFIG.overrideDefaults || {})[key];

                if (key === 'model_id') {
                    // The family/model flyout picker isn't a plain
                    // <select>/<input> -- its trigger label and family
                    // highlight are kept in sync via _syncModelSelectors()
                    // below (called once, after this loop, with the
                    // resulting effective model_id). Only the "overridden"
                    // highlight is handled here.
                    const value = this._activeConfigOverrides.model_id;
                    row.classList.toggle('overridden', isOverridden && !this._valueEqualsDefault(value, defaultValue));
                    return;
                }

                const input = row.querySelector('select, input');
                if (!input) return;

                if (isOverridden) {
                    const value = this._activeConfigOverrides[key];
                    if (input.type === 'checkbox') {
                        input.checked = !!value;
                    } else {
                        input.value = value;
                        const valueSpan = row.querySelector('.config-control-value');
                        if (valueSpan && input.type === 'range') valueSpan.textContent = value;
                    }
                    // Only highlight when the override's value actually differs
                    // from the global default -- e.g. dialing temperature back
                    // to its default value should drop the highlight even
                    // though the server is technically still tracking an
                    // override for this key.
                    row.classList.toggle('overridden', !this._valueEqualsDefault(value, defaultValue));
                } else {
                    // Not (or no longer) overridden -- reset the control back to the
                    // actual global default rather than leaving whatever value the
                    // user last dialed in (this is what makes "Reset to defaults"
                    // actually update the UI, not just clear server-side state).
                    row.classList.remove('overridden');
                    const field = ChatClient.DYNAMIC_OVERRIDE_FIELDS.find((f) => f.key === key);
                    if (input.type === 'checkbox') {
                        input.checked = !!defaultValue;
                    } else {
                        input.value = defaultValue ?? (field ? field.min : '');
                        const valueSpan = row.querySelector('.config-control-value');
                        if (valueSpan && input.type === 'range') valueSpan.textContent = input.value;
                    }
                }
            });
        }


        // Model may have changed (new override or reset) -- keep the
        // temperature/top_p controls' visibility, and the max_tokens cap, in
        // sync with the resulting effective model.
        const effectiveModelId = this._activeConfigOverrides.model_id
            || (window.CONFIG.overrideDefaults || {}).model_id
            || window.CONFIG.modelId;
        this._syncModelSelectors(effectiveModelId);
        this._updateTemperatureControlsVisibility(effectiveModelId);
        this._updateMaxTokensCapForModel(effectiveModelId);

        // Same for the KB controls, based on the resulting effective enable_rag
        // (note: false is a valid override value, so check for key presence
        // rather than truthiness).
        const effectiveEnableRag = Object.prototype.hasOwnProperty.call(this._activeConfigOverrides, 'enable_rag')
            ? this._activeConfigOverrides.enable_rag
            : !!(window.CONFIG.overrideDefaults || {}).enable_rag;
        this._updateKbControlsVisibility(effectiveEnableRag);
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
