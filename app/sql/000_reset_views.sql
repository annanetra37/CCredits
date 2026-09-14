-- ---------------------------------------------------------------------------
-- Views are the product, and they change shape as rules change. Postgres
-- refuses to rename a column via CREATE OR REPLACE VIEW, so every migration
-- drops the view graph and rebuilds it. Nothing is lost: Silver and Gold hold
-- no data of their own, only Bronze and the two reference tables do.
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

DO $$
DECLARE v record;
BEGIN
    FOR v IN
        SELECT schemaname, viewname
        FROM pg_views
        WHERE schemaname IN ('silver', 'gold')
    LOOP
        EXECUTE format('DROP VIEW IF EXISTS %I.%I CASCADE', v.schemaname, v.viewname);
    END LOOP;
END $$;
