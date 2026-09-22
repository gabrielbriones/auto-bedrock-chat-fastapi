import { HttpResponse, http } from 'msw'

import documentList from '../fixtures/knowledge/kb-document-list.json'
import document from '../fixtures/knowledge/kb-document.json'

const ADMIN = '*/bedrock-chat/admin'

export const knowledgeHandlers = [
  http.get(`${ADMIN}/kb/documents`, () => HttpResponse.json(documentList)),
  http.get(`${ADMIN}/kb/documents/:id`, () => HttpResponse.json(document)),
  http.patch(`${ADMIN}/kb/documents/:id`, () => HttpResponse.json(document)),
  http.delete(`${ADMIN}/kb/documents/:id`, () => new HttpResponse(null, { status: 204 })),
  http.post(`${ADMIN}/kb/documents/reset-credibility/:id`, () => HttpResponse.json(document)),
]