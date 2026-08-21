-- 010: Harden the money model.
--
-- The `transactions` table becomes the authoritative append-only ledger:
--   * campaign_id / bid_id link every spend row to its RTB event
--   * a partial UNIQUE index makes spend billing idempotent at the DB level
--     (duplicate win notices can never double-charge, even across replicas)
--   * currency is recorded explicitly (RUB per the spec; legacy rows default too)
--
-- All statements are additive and idempotent.

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS campaign_id UUID REFERENCES campaigns(id) ON DELETE SET NULL;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS bid_id TEXT;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'RUB';

-- One billing row per won bid: the hard idempotency guard against duplicate
-- win notices and event-tracking replay, across all replicas.
CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_spend_bid
    ON transactions (bid_id)
    WHERE type = 'spend' AND bid_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_transactions_campaign
    ON transactions (campaign_id, created_at DESC)
    WHERE campaign_id IS NOT NULL;

-- Ledger rows must never be mutated or deleted by application roles.
-- (Enforced in code review too; this trigger is the backstop.)
CREATE OR REPLACE FUNCTION forbid_ledger_mutation() RETURNS trigger AS $$
BEGIN
    -- Allow setting balance_after on the row created inside the same billing
    -- transaction (two-step insert), forbid everything else.
    IF TG_OP = 'UPDATE'
       AND OLD.balance_after IS NULL
       AND NEW.id = OLD.id
       AND NEW.user_id = OLD.user_id
       AND NEW.advertiser_id IS NOT DISTINCT FROM OLD.advertiser_id
       AND NEW.type = OLD.type
       AND NEW.amount = OLD.amount
       AND NEW.bid_id IS NOT DISTINCT FROM OLD.bid_id THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'transactions is an append-only ledger (op=%, id=%)', TG_OP, OLD.id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_transactions_append_only ON transactions;
CREATE TRIGGER trg_transactions_append_only
    BEFORE UPDATE OR DELETE ON transactions
    FOR EACH ROW EXECUTE FUNCTION forbid_ledger_mutation();
