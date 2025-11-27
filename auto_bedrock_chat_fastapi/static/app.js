// Main application initialization
document.addEventListener('DOMContentLoaded', function() {
    const authModal = document.getElementById('authModal');
    const skipAuthButton = document.getElementById('skipAuthButton');
    
    // Store chatClient globally so it persists across modal interactions
    if (!window.chatClient) {
        window.chatClient = null;
    }

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

// Helper function to process message content with markdown and remove reasoning if needed
function processMessageContent(content, modelId) {
    // Basic markdown processing
    let processed = content
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/\n/g, '<br>');

    // Remove reasoning tags for Claude models
    if (modelId && modelId.includes('claude')) {
        // Remove <reasoning> blocks but preserve content
        processed = processed.replace(/<\/?reasoning>/g, '');
    }

    return processed;
}
