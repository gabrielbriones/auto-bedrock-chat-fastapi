// Authentication functions

// --- Validation helpers ---
function markInvalid(inputId, message = 'This field is required.') {
    const el = document.getElementById(inputId);
    if (!el) return;
    el.classList.add('auth-input-error');
    el.setAttribute('aria-invalid', 'true');

    // Add inline error message if not already present
    const errorId = inputId + '-error';
    if (!document.getElementById(errorId)) {
        const errorEl = document.createElement('span');
        errorEl.id = errorId;
        errorEl.className = 'auth-error-message';
        errorEl.setAttribute('role', 'alert');
        errorEl.textContent = message;
        el.setAttribute('aria-describedby', errorId);
        el.parentNode.appendChild(errorEl);
    }
}

function clearInvalid(inputId) {
    const el = document.getElementById(inputId);
    if (!el) return;
    el.classList.remove('auth-input-error');
    el.removeAttribute('aria-invalid');
    el.removeAttribute('aria-describedby');

    const errorEl = document.getElementById(inputId + '-error');
    if (errorEl) errorEl.remove();
}

function clearAllValidation() {
    document.querySelectorAll('.auth-input-error').forEach(el => {
        el.classList.remove('auth-input-error');
        el.removeAttribute('aria-invalid');
        el.removeAttribute('aria-describedby');
    });
    document.querySelectorAll('.auth-error-message').forEach(el => el.remove());
}

// Clear error highlight as soon as the user starts typing (scoped to auth form)
function attachValidationListeners() {
    const authForm = document.getElementById('authForm');
    if (authForm && !authForm.dataset.validationListenerAttached) {
        authForm.addEventListener('input', (e) => {
            if (e.target.classList.contains('auth-input-error')) {
                clearInvalid(e.target.id);
            }
        });
        authForm.dataset.validationListenerAttached = 'true';
    }
}

function initializeAuthModal() {
    const supportedTypes = window.CONFIG.supportedAuthTypes;
    const authTypeSelector = document.getElementById('authTypeSelector');
    const authTypeSelect = document.getElementById('authType');
    const authForm = document.getElementById('authForm');
    const skipButton = document.getElementById('skipAuthButton');

    // Populate auth type dropdown if empty
    if (authTypeSelect.options.length === 1 && supportedTypes.length > 0) {
        supportedTypes.forEach(authType => {
            const option = document.createElement('option');
            option.value = authType;
            const displayText = authType.replace(/_/g, ' ');
            option.textContent = displayText.charAt(0).toUpperCase() + displayText.slice(1);
            authTypeSelect.appendChild(option);
        });
    }

    // Hide/show skip button based on whether auth is required
    if (skipButton) {
        skipButton.style.display = window.CONFIG.requireAuth ? 'none' : 'block';
    }

    // If only one auth type, hide selector and auto-select it
    if (supportedTypes.length === 1) {
        authTypeSelector.classList.add('hidden');
        authTypeSelect.value = supportedTypes[0];
        updateAuthFields();
    }

    // Attach event handlers only if not already attached
    if (authTypeSelect && !authTypeSelect.dataset.listenerAttached) {
        authTypeSelect.addEventListener('change', updateAuthFields);
        authTypeSelect.dataset.listenerAttached = 'true';
    }

    if (authForm && !authForm.dataset.listenerAttached) {
        authForm.addEventListener('submit', (e) => {
            e.preventDefault();
            submitAuth();
        });
        authForm.dataset.listenerAttached = 'true';
    }

    if (skipButton && !skipButton.dataset.listenerAttached) {
        skipButton.addEventListener('click', skipAuth);
        skipButton.dataset.listenerAttached = 'true';
    }

    attachValidationListeners();
}

function updateAuthFields() {
    const authType = document.getElementById('authType').value;
    const fieldsContainer = document.getElementById('authFields');

    // Clear any validation highlights from the previous auth type
    clearAllValidation();

    // Get all field group divs
    const allFieldGroups = fieldsContainer.querySelectorAll('div[id$="-fields"]');

    // Hide all fields and clear their values
    allFieldGroups.forEach(fieldGroup => {
        const isCurrentType = fieldGroup.id === authType + '-fields';

        // Add hidden class to non-selected types
        if (!isCurrentType) {
            fieldGroup.classList.add('auth-field-hidden');

            // Clear all fields to prevent credential leakage when switching auth types
            fieldGroup.querySelectorAll('input, textarea').forEach(input => {
                if (input.id === 'apiKeyHeader') {
                    input.value = 'X-API-Key';  // Reset to default
                } else {
                    input.value = '';
                }
            });
        }
    });

    // Show selected auth type fields
    if (authType) {
        const fieldId = authType + '-fields';
        const fieldEl = document.getElementById(fieldId);
        if (fieldEl) {
            fieldEl.classList.remove('auth-field-hidden');
        }
    }
}

function getAuthPayload() {
    const authType = document.getElementById('authType').value;

    if (!authType) return null;

    clearAllValidation();

    const payload = { type: 'auth', auth_type: authType };
    const missing = [];

    switch (authType) {
        case 'bearer_token':
            payload.token = document.getElementById('bearerToken').value;
            if (!payload.token) missing.push('bearerToken');
            break;
        case 'basic_auth':
            payload.username = document.getElementById('username').value;
            payload.password = document.getElementById('password').value;
            if (!payload.username) missing.push('username');
            if (!payload.password) missing.push('password');
            break;
        case 'api_key':
            payload.api_key = document.getElementById('apiKey').value;
            payload.api_key_header = document.getElementById('apiKeyHeader').value;
            if (!payload.api_key) missing.push('apiKey');
            if (!payload.api_key_header) missing.push('apiKeyHeader');
            break;
        case 'oauth2_client_credentials':
            payload.client_id = document.getElementById('clientId').value;
            payload.client_secret = document.getElementById('clientSecret').value;
            payload.token_url = document.getElementById('tokenUrl').value;
            if (!payload.client_id) missing.push('clientId');
            if (!payload.client_secret) missing.push('clientSecret');
            if (!payload.token_url) missing.push('tokenUrl');
            const scope = document.getElementById('scope').value;
            if (scope) payload.scope = scope;
            break;
        case 'custom':
            try {
                const customHeadersText = document.getElementById('customHeaders').value;
                if (!customHeadersText.trim()) {
                    missing.push({ id: 'customHeaders' });
                } else {
                    payload.custom_headers = JSON.parse(customHeadersText);
                }
            } catch (e) {
                missing.push({ id: 'customHeaders', message: 'Invalid JSON syntax.' });
            }
            break;
    }

    if (missing.length > 0) {
        missing.forEach(entry => {
            if (typeof entry === 'string') {
                markInvalid(entry);
            } else {
                markInvalid(entry.id, entry.message);
            }
        });
        // Focus the first invalid field
        const firstId = typeof missing[0] === 'string' ? missing[0] : missing[0].id;
        const first = document.getElementById(firstId);
        if (first) first.focus();
        return null;
    }

    return payload;
}

function submitAuth() {
    const payload = getAuthPayload();
    if (!payload) return;

    // Disable submit button to prevent multiple submissions
    const submitBtn = document.querySelector('.auth-submit');
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = 'Authenticating...';
    }

    console.log('submitAuth: Setting up authentication...');

    // If there's already an open connection, reuse it by just sending a
    // new auth message.  This avoids creating a brand-new session on
    // every retry (e.g. after entering wrong credentials).
    if (window.chatClient && window.chatClient.ws &&
        window.chatClient.ws.readyState === WebSocket.OPEN) {
        console.log('submitAuth: Reusing existing connection, sending new auth payload');
        window.chatClient.authPayload = payload;
        window.chatClient.authSent = false;
        window.chatClient.sendAuth();
    } else {
        // No usable connection — create a fresh one
        if (window.chatClient && window.chatClient.ws) {
            console.log('submitAuth: Closing stale connection');
            window.chatClient.intentionalClose = true;
            window.chatClient.ws.close();
        }
        console.log('submitAuth: Creating new ChatClient with auth credentials');
        window.chatClient = new ChatClient(payload);
    }

    // Don't hide auth modal yet — wait for server to confirm via
    // auth_configured (success) or auth_failed (failure) message.
    // The modal will be hidden by handleMessage() on auth_configured,
    // or re-shown with an error on auth_failed.
}

function skipAuth() {
    if (window.CONFIG.requireAuth) {
        alert('Authentication is required to use this chat.');
        return;  // Don't close modal or initialize chat
    }

    // Just hide modal, use existing connection or create new one without auth
    if (!window.chatClient || !window.chatClient.ws || window.chatClient.ws.readyState !== WebSocket.OPEN) {
        window.chatClient = new ChatClient();
    }
    document.getElementById('authModal').classList.add('hidden');
}
