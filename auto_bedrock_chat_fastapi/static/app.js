// Main application initialization
document.addEventListener('DOMContentLoaded', function() {
    const authModal = document.getElementById('authModal');
    const skipAuthButton = document.getElementById('skipAuthButton');

    // Check if auth is enabled
    const authEnabled = window.CONFIG.authEnabled;
    const requireAuth = window.CONFIG.requireAuth;
    const ssoEnabled  = window.CONFIG.ssoEnabled;

    // Handle skip auth button (form-based auth only)
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

    // Initialize chat client
    if (!authEnabled) {
        // Auth completely disabled — start immediately with no modal
        authModal.classList.add('hidden');
        if (!window.chatClient) {
            window.chatClient = new ChatClient();
        }
    } else if (ssoEnabled) {
        // SSO path: authenticate transparently using the server-side session.
        // initializeSSOAuth() is defined in auth.js and handles the /auth/token
        // fetch plus WebSocket setup — the user never sees a credential form.
        initializeSSOAuth();
    } else if (!requireAuth) {
        // Form-based auth enabled but not required — hide modal and start chat
        authModal.classList.add('hidden');
        if (!window.chatClient) {
            window.chatClient = new ChatClient();
        }
    } else {
        // Form-based auth required — keep modal visible and render the form
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
