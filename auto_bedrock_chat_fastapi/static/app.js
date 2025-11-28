// Main application initialization
document.addEventListener('DOMContentLoaded', function() {
    const authModal = document.getElementById('authModal');
    const skipAuthButton = document.getElementById('skipAuthButton');

    // Check if auth is enabled
    const authEnabled = window.CONFIG.authEnabled;
    const requireAuth = window.CONFIG.requireAuth;

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
    // Remove reasoning tags for Claude models
    let processed = content;
    if (modelId && modelId.includes('claude')) {
        // Remove <reasoning>...</reasoning> blocks including content
        processed = processed.replace(/<reasoning[^>]*>[\s\S]*?<\/reasoning>/gi, '');
    }

    // Use marked.js for markdown processing (handles escaping properly)
    if (window.marked) {
        processed = marked.parse(processed);
    }

    return processed;
}
