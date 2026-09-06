-- 037: idempotent manual money operations (audit M-02).
--
-- /billing/deposit and /billing/adjust accept an Idempotency-Key. The key is
-- stored on the ledger row; a partial UNIQUE index makes a retried request
-- (client timeout, double click, replayed webhook) hit the constraint instead
-- of crediting the balance twice. Spend rows keep their own guard
-- (uq_transactions_spend_bid) and never carry an idempotency key.
--
-- Additive and idempotent.

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_idempotency_key
    ON transactions (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
