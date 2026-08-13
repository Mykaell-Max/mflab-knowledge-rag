CREATE SCHEMA IF NOT EXISTS mflab_knowledge;

CREATE TABLE IF NOT EXISTS mflab_knowledge.repositories (
    repository_id text PRIMARY KEY,
    project text NOT NULL,
    remote_url text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mflab_knowledge.documents (
    document_id text PRIMARY KEY,
    repository_id text NOT NULL REFERENCES mflab_knowledge.repositories(repository_id)
        ON DELETE CASCADE,
    path text NOT NULL,
    format text NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    content_hash text NOT NULL,
    access_class text NOT NULL CHECK (
        access_class IN ('public', 'lab', 'project', 'restricted', 'pending')
    ),
    encoding text,
    parser_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS documents_repository_path_idx
    ON mflab_knowledge.documents (repository_id, path);
CREATE INDEX IF NOT EXISTS documents_access_idx
    ON mflab_knowledge.documents (access_class);

CREATE TABLE IF NOT EXISTS mflab_knowledge.document_occurrences (
    occurrence_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id text NOT NULL REFERENCES mflab_knowledge.documents(document_id)
        ON DELETE CASCADE,
    branch text,
    commit_sha text,
    canonical boolean NOT NULL DEFAULT false,
    requested_ref text
);

CREATE INDEX IF NOT EXISTS document_occurrences_document_idx
    ON mflab_knowledge.document_occurrences (document_id);
CREATE INDEX IF NOT EXISTS document_occurrences_branch_idx
    ON mflab_knowledge.document_occurrences (branch, document_id);
CREATE UNIQUE INDEX IF NOT EXISTS document_occurrences_identity_idx
    ON mflab_knowledge.document_occurrences (
        document_id,
        coalesce(branch, ''),
        coalesce(commit_sha, ''),
        coalesce(requested_ref, '')
    );

CREATE TABLE IF NOT EXISTS mflab_knowledge.chunks (
    chunk_id text PRIMARY KEY,
    document_id text NOT NULL REFERENCES mflab_knowledge.documents(document_id)
        ON DELETE CASCADE,
    title text NOT NULL,
    kind text NOT NULL,
    line_start integer NOT NULL CHECK (line_start >= 1),
    line_end integer NOT NULL CHECK (line_end >= line_start),
    text text NOT NULL,
    chunk_hash text NOT NULL,
    embedding_key text NOT NULL,
    parser_version text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(text, '')), 'B')
    ) STORED,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chunks_document_idx
    ON mflab_knowledge.chunks (document_id);
CREATE INDEX IF NOT EXISTS chunks_hash_idx
    ON mflab_knowledge.chunks (chunk_hash);
CREATE INDEX IF NOT EXISTS chunks_search_idx
    ON mflab_knowledge.chunks USING GIN (search_vector);

CREATE TABLE IF NOT EXISTS mflab_knowledge.ingestion_runs (
    run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    repository_id text NOT NULL REFERENCES mflab_knowledge.repositories(repository_id)
        ON DELETE CASCADE,
    documents_hash text NOT NULL,
    chunks_hash text NOT NULL,
    documents_count integer NOT NULL,
    chunks_count integer NOT NULL,
    completed_at timestamptz NOT NULL DEFAULT now()
);
