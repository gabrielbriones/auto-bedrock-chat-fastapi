import { createFileRoute } from '@tanstack/react-router';

import { ChatLayout } from '@/app/layouts/ChatLayout';
import { ConversationView } from '@/app/chat/ConversationView';

function ChatRoute() {
  return (
    <ChatLayout>
      <ConversationView conversationId={null} />
    </ChatLayout>
  );
}

// SPEC-021 §2: /ui — a new conversation.
export const Route = createFileRoute('/ui/')({
  component: ChatRoute,
});
