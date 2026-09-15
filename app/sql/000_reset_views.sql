-- ---------------------------------------------------------------------------
-- Views are the product, and they change shape as rules change. Postgres
-- refuses to rename a column via CREATE OR REPLACE VIEW, so every migration
-- drops the view graph and rebuilds it. Nothing is lost: Silver and Gold hold
-- no data of their own, only Bronze, ops.visit and the two reference tables do.
-- ops belongs in this list for the same reason the others do: a view there
-- that gains a column cannot be replaced in place either.
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS ops;

DO $$
DECLARE v record;
BEGIN
    FOR v IN
        SELECT schemaname, viewname
        FROM pg_views
        WHERE schemaname IN ('silver', 'gold', 'ops')
    LOOP
        EXECUTE format('DROP VIEW IF EXISTS %I.%I CASCADE', v.schemaname, v.viewname);
    END LOOP;
END $$;
