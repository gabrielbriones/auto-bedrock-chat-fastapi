import { HttpResponse, http } from 'msw'

import documentList from '../fixtures/knowledge/kb-document-list.json' with { type: 'json' }
import document from '../fixtures/knowledge/kb-document.json' with { type: 'json' }
import sourceRun from '../fixtures/knowledge/kb-source-run.json' with { type: 'json' }
import sourceStatusIdle from '../fixtures/knowledge/kb-source-status-idle.json' with { type: 'json' }
import sources from '../fixtures/knowledge/kb-sources.json' with { type: 'json' }

const ADMIN = '*/chat/admin'

const accepted = () => HttpResponse.json(sourceRun, { status: 202 })

export const knowledgeHandlers = [
  http.get(`${ADMIN}/kb/documents`, () => HttpResponse.json(documentList)),
  http.get(`${ADMIN}/kb/documents/:id`, () => HttpResponse.json(document)),
  http.patch(`${ADMIN}/kb/documents/:id`, () => HttpResponse.json(document)),
  http.delete(`${ADMIN}/kb/documents/:id`, () => new HttpResponse(null, { status: 204 })),
  http.post(`${ADMIN}/kb/documents/reset-credibility/:id`, () => HttpResponse.json(document)),
  // KB source ingestion (admin-api.md "KB Source Ingestion").
  http.get(`${ADMIN}/kb/sources`, () => HttpResponse.json(sources)),
  http.get(`${ADMIN}/kb/sources/status`, () => HttpResponse.json(sourceStatusIdle)),
  http.post(`${ADMIN}/kb/sources/web`, accepted),
  http.put(`${ADMIN}/kb/sources/web/:name`, accepted),
  http.post(`${ADMIN}/kb/sources/file`, accepted),
  http.put(`${ADMIN}/kb/sources/file/:name`, accepted),
  http.delete(`${ADMIN}/kb/sources/:name`, ({ params }) =>
    HttpResponse.json({ source: params.name, deleted: 4 }),
  ),
]
