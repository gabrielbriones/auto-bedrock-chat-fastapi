import { createFileRoute } from '@tanstack/react-router';

import { ChatLayout } from '@/app/layouts/ChatLayout';
import { ConversationView } from '@/app/chat/ConversationView';
import { conversationId as toConversationId } from '@/shared/kernel/branded';

// SPEC-021 §2 / FR-CONV-011: /chat/ui/c/:conversationId — the same ChatPanel, loading that conversation.
export const Route = createFileRoute('/ui/c/$conversationId')({
  component: ConversationRoute,
});

function ConversationRoute() {
  const { conversationId } = Route.useParams();

  return (
    <ChatLayout>
      <ConversationView conversationId={toConversationId(conversationId)} />
    </ChatLayout>
  );
}
