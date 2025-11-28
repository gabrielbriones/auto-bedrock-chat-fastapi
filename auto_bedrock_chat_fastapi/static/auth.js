// Authentication functions
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
}

function updateAuthFields() {
    const authType = document.getElementById('authType').value;
    const fieldsContainer = document.getElementById('authFields');

    // Get all field group divs
    const allFieldGroups = fieldsContainer.querySelectorAll('div[id$="-fields"]');

    // Hide all fields and clear their values
    allFieldGroups.forEach(fieldGroup => {
        // Add hidden class
        fieldGroup.classList.add('auth-field-hidden');

        // Clear all input and textarea values
        fieldGroup.querySelectorAll('input, textarea').forEach(input => {
            if (input.id === 'apiKeyHeader') {
                input.value = 'X-API-Key';  // Reset to default
            } else {
                input.value = '';
            }
        });
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

    const payload = { type: 'auth', auth_type: authType };

    switch (authType) {
        case 'bearer_token':
            payload.token = document.getElementById('bearerToken').value;
            break;
        case 'basic_auth':
            payload.username = document.getElementById('username').value;
            payload.password = document.getElementById('password').value;
            break;
        case 'api_key':
            payload.api_key = document.getElementById('apiKey').value;
            payload.api_key_header = document.getElementById('apiKeyHeader').value;
            break;
        case 'oauth2_client_credentials':
            payload.client_id = document.getElementById('clientId').value;
            payload.client_secret = document.getElementById('clientSecret').value;
            payload.token_url = document.getElementById('tokenUrl').value;
            const scope = document.getElementById('scope').value;
            if (scope) payload.scope = scope;
            break;
        case 'custom':
            try {
                const customHeadersText = document.getElementById('customHeaders').value;
                payload.custom_headers = JSON.parse(customHeadersText);
            } catch (e) {
                const fullInput = document.getElementById('customHeaders').value;
                const preview = fullInput.substring(0, 100);
                alert(`Invalid JSON for custom headers.\n\nError: ${e.message}\n\nYour input: ${preview}${fullInput.length > 100 ? '...' : ''}`);
                return null;
            }
            break;
    }

    return payload;
}

function submitAuth() {
    const payload = getAuthPayload();
    if (!payload) {
        alert('Please fill in all required fields');
        return;
    }

    console.log('submitAuth: Checking existing connection...');
    // Send auth through existing connection or create new one with auth
    if (window.chatClient && window.chatClient.ws && window.chatClient.ws.readyState === WebSocket.OPEN) {
        // Send auth through existing connection
        console.log('submitAuth: Using existing connection');
        window.chatClient.authPayload = payload;
        window.chatClient.sendAuth();
    } else {
        // Create new chat client with auth payload
        console.log('submitAuth: Creating new ChatClient with auth');
        window.chatClient = new ChatClient(payload);
    }
    document.getElementById('authModal').classList.add('hidden');
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
