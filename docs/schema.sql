-- GENERATED FILE -- do not edit.
-- Source of truth is migrations/versions/*.py (SQLAlchemy models in app/db/models).
-- Regenerate with:  alembic upgrade head --sql > docs/schema.sql

BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001

CREATE TABLE documents (
    id UUID NOT NULL, 
    original_filename TEXT NOT NULL, 
    content_hash VARCHAR(64) NOT NULL, 
    size_bytes INTEGER NOT NULL, 
    page_count INTEGER, 
    status VARCHAR(32) NOT NULL, 
    error_message TEXT, 
    source_path TEXT, 
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    expired_at TIMESTAMP WITH TIME ZONE, 
    claimed_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_documents PRIMARY KEY (id), 
    CONSTRAINT ck_documents_status_valid CHECK (status IN ('pending','converting','extracting','completed','partial_failed','failed'))
);

CREATE UNIQUE INDEX uq_documents_content_hash_live ON documents (content_hash) WHERE expired_at IS NULL;

CREATE INDEX ix_documents_created_at ON documents (created_at DESC);

CREATE TABLE extraction_engines (
    key VARCHAR(64) NOT NULL, 
    provider VARCHAR(64) NOT NULL, 
    model_name VARCHAR(128) NOT NULL, 
    version VARCHAR(32) NOT NULL, 
    endpoint_url TEXT, 
    unit VARCHAR(16) NOT NULL, 
    unit_cost NUMERIC(12, 6), 
    currency VARCHAR(3), 
    is_active BOOLEAN NOT NULL, 
    config JSONB, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_extraction_engines PRIMARY KEY (key), 
    CONSTRAINT ck_extraction_engines_unit_valid CHECK (unit IN ('page','request','1k_tokens'))
);

CREATE TABLE pages (
    id UUID NOT NULL, 
    document_id UUID NOT NULL, 
    page_no INTEGER NOT NULL, 
    image_path TEXT, 
    status VARCHAR(32) NOT NULL, 
    requested_engine_key VARCHAR(64), 
    claimed_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_pages PRIMARY KEY (id), 
    CONSTRAINT ck_pages_page_no_positive CHECK (page_no >= 1), 
    CONSTRAINT ck_pages_status_valid CHECK (status IN ('pending','processing','succeeded','failed')), 
    CONSTRAINT fk_pages_document_id_documents FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, 
    CONSTRAINT fk_pages_requested_engine_key_extraction_engines FOREIGN KEY(requested_engine_key) REFERENCES extraction_engines (key) ON DELETE RESTRICT, 
    CONSTRAINT uq_pages_document_id_page_no UNIQUE (document_id, page_no)
);

CREATE TABLE extractions (
    id UUID NOT NULL, 
    page_id UUID NOT NULL, 
    engine_key VARCHAR(64) NOT NULL, 
    attempt_no INTEGER NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    raw_response JSONB, 
    error_code INTEGER, 
    error_message TEXT, 
    latency_ms INTEGER, 
    usage_qty NUMERIC(12, 4), 
    engine_version VARCHAR(32), 
    unit_cost_snapshot NUMERIC(12, 6), 
    currency VARCHAR(3), 
    cost_amount NUMERIC(14, 6), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_extractions PRIMARY KEY (id), 
    CONSTRAINT ck_extractions_attempt_no_positive CHECK (attempt_no >= 1), 
    CONSTRAINT ck_extractions_status_valid CHECK (status IN ('succeeded','failed')), 
    CONSTRAINT fk_extractions_engine_key_extraction_engines FOREIGN KEY(engine_key) REFERENCES extraction_engines (key) ON DELETE RESTRICT, 
    CONSTRAINT fk_extractions_page_id_pages FOREIGN KEY(page_id) REFERENCES pages (id) ON DELETE CASCADE, 
    CONSTRAINT uq_extractions_page_engine_attempt UNIQUE (page_id, engine_key, attempt_no)
);

CREATE INDEX ix_extractions_page_id_created_at ON extractions (page_id, created_at DESC);

INSERT INTO extraction_engines
            (key, provider, model_name, version, endpoint_url, unit,
             unit_cost, currency, is_active, config)
        VALUES
            ('fa_receipt_v1_5', 'FastAccounting', 'receipt', 'v1.5',
             'https://api-gt01-sandbox.fastaccounting.jp/fa/v1.5/receipt',
             'page', NULL, NULL, true, '{}'::jsonb);

INSERT INTO alembic_version (version_num) VALUES ('0001') RETURNING alembic_version.version_num;

COMMIT;

