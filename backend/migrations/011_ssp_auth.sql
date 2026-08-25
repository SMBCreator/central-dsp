-- 011: SSP authentication token.
-- Additive and backward-compatible: partners with auth_token = NULL keep
-- working with the URL endpoint_key only; once a token is set the bid
-- endpoint additionally requires the X-CentralDSP-Token header.
ALTER TABLE supply_partners ADD COLUMN IF NOT EXISTS auth_token TEXT;

COMMENT ON COLUMN supply_partners.auth_token IS
  'Optional shared secret. When set, POST /rtb/bid/{endpoint_key} requires header X-CentralDSP-Token to match (constant-time compare).';
