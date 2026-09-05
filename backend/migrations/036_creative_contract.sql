-- 036: договор ОРД на креативе + автоматическая маркировка после модерации.
--
-- Зачем: до этой миграции регистрация креатива в ОРД (получение erid) была
-- отдельным ручным шагом на странице «Маркировка», и одобренный модератором
-- креатив мог попасть в фид биддера без erid. Теперь approve автоматически
-- регистрирует креатив в ОРД, а для этого нужно знать, по какому договору.
--
--   creatives.contract_id — явный выбор договора на шаге «Маркировка».
--   Если NULL, при approve берётся единственный зарегистрированный договор,
--   где client_id = creatives.counterparty_id (см. ord/service.py).
--
-- ord_creatives.status получает новое значение 'awaiting_contract' —
-- креатив одобрен, но договор для регистрации не найден; владелец
-- уведомлён, в фид для РФ-трафика креатив не попадает.

ALTER TABLE creatives ADD COLUMN IF NOT EXISTS contract_id UUID REFERENCES ord_contracts(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_creatives_contract ON creatives(contract_id) WHERE contract_id IS NOT NULL;

COMMENT ON COLUMN creatives.contract_id IS 'Договор ОРД, по которому креатив регистрируется для получения erid (NULL = подобрать по контрагенту)';
COMMENT ON COLUMN creatives.erid IS 'Токен маркировки ОРД; присваивается автоматически после approve. Без erid креатив не попадает в фид для РФ-трафика';
