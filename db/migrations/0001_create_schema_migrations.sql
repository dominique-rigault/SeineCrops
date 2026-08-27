-- db/migrations/0001_create_schema_migrations.sql
BEGIN;

CREATE TABLE IF NOT EXISTS public.schema_migrations (
    version     text PRIMARY KEY,
    description text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO public.schema_migrations (version, description)
VALUES ('0001', 'creation table de suivi des migrations')
ON CONFLICT (version) DO NOTHING;

COMMIT;
