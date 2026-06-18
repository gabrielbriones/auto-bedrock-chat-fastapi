CREATE TABLE
    IF NOT EXISTS documents (
        id TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        title TEXT,
        source TEXT,
        source_url TEXT,
        topic TEXT,
        date_published TEXT,
        metadata TEXT,
        created_at TIMESTAMPTZ DEFAULT now ()
    );

CREATE TABLE IF NOT EXISTS chunks (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES documents(id),
    content      TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    start_char   INTEGER,
    end_char     INTEGER,
    metadata     TEXT,
    embedding    vector({embedding_dimensions}),
    content_tsv  tsvector
        GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source);
CREATE INDEX IF NOT EXISTS idx_documents_topic ON documents(topic);
CREATE INDEX IF NOT EXISTS idx_documents_date ON documents(date_published);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);

CREATE INDEX IF NOT EXISTS idx_chunks_embedding
ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_chunks_fts ON chunks USING gin(content_tsv);
