-- 023: human-friendly sequential display IDs for all entities.
-- Each table gets its own sequence starting at 1; existing rows are
-- numbered by creation order (created_at, then uuid for stability).

DO $$
DECLARE
    t TEXT;
    tables TEXT[] := ARRAY[
        'users', 'advertisers', 'campaigns', 'campaign_groups',
        'line_items', 'creatives', 'supply_partners', 'counterparties'
    ];
BEGIN
    FOREACH t IN ARRAY tables LOOP
        IF to_regclass(t) IS NULL THEN
            CONTINUE;
        END IF;

        -- add column if missing
        EXECUTE format(
            'ALTER TABLE %I ADD COLUMN IF NOT EXISTS display_id BIGINT', t
        );

        -- create sequence if missing
        EXECUTE format(
            'CREATE SEQUENCE IF NOT EXISTS %I OWNED BY %I.display_id',
            t || '_display_id_seq', t
        );

        -- backfill existing rows in creation order
        EXECUTE format(
            'WITH ordered AS (
                 SELECT id, row_number() OVER (ORDER BY created_at NULLS LAST, id) AS rn
                 FROM %I WHERE display_id IS NULL
             )
             UPDATE %I SET display_id = ordered.rn + COALESCE(
                 (SELECT max(display_id) FROM %I), 0
             )
             FROM ordered WHERE %I.id = ordered.id',
            t, t, t, t
        );

        -- sync sequence to max
        EXECUTE format(
            'SELECT setval(%L, COALESCE((SELECT max(display_id) FROM %I), 0) + 1, false)',
            t || '_display_id_seq', t
        );

        -- default for new rows + constraints
        EXECUTE format(
            'ALTER TABLE %I ALTER COLUMN display_id SET DEFAULT nextval(%L)',
            t, t || '_display_id_seq'
        );
        EXECUTE format(
            'ALTER TABLE %I ALTER COLUMN display_id SET NOT NULL', t
        );
        EXECUTE format(
            'CREATE UNIQUE INDEX IF NOT EXISTS %I ON %I (display_id)',
            t || '_display_id_key', t
        );
    END LOOP;
END $$;
