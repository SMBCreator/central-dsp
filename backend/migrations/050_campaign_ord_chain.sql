-- B13d (ord_spec §7 D2): цепочка ОРД живёт на кампании, креативы наследуют.
-- ord_mode: self (свои товары/услуги), own_client (свой клиент, прямой договор),
-- partner_client (клиент партнёра, через посредника). После подтверждения
-- (ord_confirmed_at) поля цепочки заморожены — как «Данные ОРД» в Avito.
ALTER TABLE campaigns
    ADD COLUMN IF NOT EXISTS ord_mode TEXT,
    ADD COLUMN IF NOT EXISTS counterparty_id UUID REFERENCES counterparties(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS initial_contract_id UUID REFERENCES ord_contracts(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS executive_contract_id UUID REFERENCES ord_contracts(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS ord_confirmed_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'campaigns_ord_mode_check') THEN
        ALTER TABLE campaigns ADD CONSTRAINT campaigns_ord_mode_check
            CHECK (ord_mode IS NULL OR ord_mode IN ('self', 'own_client', 'partner_client'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_campaigns_counterparty ON campaigns(counterparty_id) WHERE counterparty_id IS NOT NULL;

-- Бэкфилл из креативов: кампания, у которой все креативы с контрагентом имеют
-- один и тот же counterparty_id (и один contract_id, если он есть), получает
-- эту цепочку. Если хотя бы один креатив уже получил erid — цепочка фактически
-- использована в ОРД, поэтому фиксируем ord_confirmed_at (иначе работающие
-- кампании упёрлись бы в блокер ord_chain при следующем запуске).
-- Режим: договор с нашей организацией (ОРС) как исполнителем и контрагентом
-- как заказчиком = self (единственный договор — исполнительский); иначе
-- own_client с изначальным договором и без исполнительского.
WITH agg AS (
    SELECT cr.campaign_id,
           min(cr.counterparty_id::text)::uuid AS cp_id,
           count(DISTINCT cr.counterparty_id)  AS cp_n,
           min(cr.contract_id::text)::uuid     AS ct_id,
           count(DISTINCT cr.contract_id)      AS ct_n,
           min(cr.ord_locked_at)               AS locked_at,
           bool_or(cr.erid IS NOT NULL)        AS has_erid
    FROM creatives cr
    WHERE cr.counterparty_id IS NOT NULL
    GROUP BY cr.campaign_id
), pick AS (
    SELECT a.campaign_id, a.cp_id,
           CASE WHEN a.ct_n = 1 THEN a.ct_id END AS ct_id,
           CASE WHEN a.has_erid THEN COALESCE(a.locked_at, now()) END AS confirmed_at,
           c.contractor_id, self.id AS self_id
    FROM agg a
    LEFT JOIN ord_contracts c ON a.ct_n = 1 AND c.id = a.ct_id
    LEFT JOIN counterparties self ON self.is_self
    WHERE a.cp_n = 1
)
UPDATE campaigns cm
   SET counterparty_id = p.cp_id,
       ord_mode = CASE
                    WHEN p.ct_id IS NOT NULL AND p.self_id IS NOT NULL AND p.contractor_id = p.self_id THEN 'self'
                    ELSE 'own_client'
                  END,
       initial_contract_id = CASE
                    WHEN p.ct_id IS NOT NULL AND p.self_id IS NOT NULL AND p.contractor_id = p.self_id THEN NULL
                    ELSE p.ct_id
                  END,
       executive_contract_id = CASE
                    WHEN p.ct_id IS NOT NULL AND p.self_id IS NOT NULL AND p.contractor_id = p.self_id THEN p.ct_id
                  END,
       ord_confirmed_at = p.confirmed_at
  FROM pick p
 WHERE cm.id = p.campaign_id AND cm.ord_mode IS NULL AND cm.counterparty_id IS NULL;
