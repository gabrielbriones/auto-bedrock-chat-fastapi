// Main application initialization
document.addEventListener('DOMContentLoaded', function() {
    const authModal = document.getElementById('authModal');
    const skipAuthButton = document.getElementById('skipAuthButton');

    // Check if auth is enabled
    const authEnabled = window.CONFIG.authEnabled;
    const requireAuth = window.CONFIG.requireAuth;

    // SSO session detection: the server validates the HttpOnly cookie and
    // passes ssoAuthenticated=true in the template context when the user has
    // a valid SSO session.  This avoids unreliable client-side heuristics
    // (document.referrer is stripped after cross-origin redirects).
    const ssoAuthenticated = window.CONFIG.ssoAuthenticated || false;

    // Update SSO user display when authenticated.
    // Logout is handled by ChatClient.handleAuthButtonClick() which checks
    // window.CONFIG.ssoAuthenticated to decide between HTTP and WS logout.
    if (ssoAuthenticated) {
        const ssoUserDisplay = document.getElementById('ssoUserDisplay');
        const authButton = document.getElementById('authButton');
        if (ssoUserDisplay && window.CONFIG.ssoUserDisplay) {
            ssoUserDisplay.textContent = window.CONFIG.ssoUserDisplay;
            ssoUserDisplay.style.display = 'inline-block';
        }
        if (authButton) {
            authButton.textContent = 'Log out';
        }
    }

    // Handle skip auth button
    if (skipAuthButton) {
        skipAuthButton.addEventListener('click', function() {
            if (requireAuth) {
                alert('Authentication is required to use this chat. Please log in.');
                return;
            }

            // Hide modal and proceed without auth
            authModal.classList.add('hidden');
            if (!window.chatClient) {
                window.chatClient = new ChatClient();
            }
        });
    }

    // If the user has an active SSO session, skip the auth modal —
    // the HttpOnly cookie will auto-authenticate the WebSocket connection.
    if (ssoAuthenticated) {
        authModal.classList.add('hidden');
        window.chatClient = new ChatClient();
        return;
    }

    // Initialize chat client
    // If auth is not enabled, start chat immediately without modal
    if (!authEnabled) {
        authModal.classList.add('hidden');
        if (!window.chatClient) {
            window.chatClient = new ChatClient();
        }
    } else if (!requireAuth) {
        // Auth is enabled but not required, hide modal and start chat
        authModal.classList.add('hidden');
        if (!window.chatClient) {
            window.chatClient = new ChatClient();
        }
    } else {
        // Auth is required, keep modal visible
        authModal.classList.remove('hidden');
        initializeAuthModal();
    }
});

// Helper function to process message content with markdown rendering
function processMessageContent(content, modelId) {
    // Remove reasoning tags for OpenAI o1 models
    let processed = content;
    if (modelId && (modelId.includes('o1') || modelId.includes('openai'))) {
        // Remove <reasoning>...</reasoning> blocks including content
        processed = processed.replace(/<reasoning[^>]*>[\s\S]*?<\/reasoning>/gi, '');
    }

    // Use marked.js for markdown processing (handles escaping properly)
    if (window.marked) {
        processed = marked.parse(processed);
    }

    return processed;
}
