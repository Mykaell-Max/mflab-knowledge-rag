CREATE TABLE IF NOT EXISTS mflab_knowledge.embedding_models (
    model_id text PRIMARY KEY,
    source_model text NOT NULL,
    source_revision text NOT NULL,
    dimensions integer NOT NULL CHECK (dimensions = 1024),
    max_sequence_length integer NOT NULL CHECK (max_sequence_length > 0),
    query_prompt text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mflab_knowledge.chunk_embeddings (
    chunk_id text NOT NULL REFERENCES mflab_knowledge.chunks(chunk_id)
        ON DELETE CASCADE,
    model_id text NOT NULL REFERENCES mflab_knowledge.embedding_models(model_id)
        ON DELETE CASCADE,
    embedding vector(1024) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_id, model_id)
);

CREATE INDEX IF NOT EXISTS chunk_embeddings_model_idx
    ON mflab_knowledge.chunk_embeddings (model_id, chunk_id);

CREATE TABLE IF NOT EXISTS mflab_knowledge.embedding_runs (
    run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_id text NOT NULL REFERENCES mflab_knowledge.embedding_models(model_id)
        ON DELETE CASCADE,
    chunks_embedded integer NOT NULL CHECK (chunks_embedded >= 0),
    chunks_reused integer NOT NULL CHECK (chunks_reused >= 0),
    device text NOT NULL,
    completed_at timestamptz NOT NULL DEFAULT now()
);
