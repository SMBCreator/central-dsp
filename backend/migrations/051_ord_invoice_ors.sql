-- B13e (ord_spec §7): акты по ОРС-модели.
-- Акт заключается по ИСПОЛНИТЕЛЬСКОМУ договору (заказчик — агентство или сам
-- рекламодатель, исполнитель — наша организация, ОРС). Внутри акта суммы
-- разбиваются по ИЗНАЧАЛЬНЫМ договорам кампаний (contracts[] в ОРД), креативы
-- привязываются к акту по изначальному договору. Сумма хранится с НДС
-- (amount_gross, как в первичке) и без (amount_net, для сверки с фактом);
-- ставка НДС — vat_rate (22 % с 2026 года; 0 для договоров без НДС).

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'ord_invoices' AND column_name = 'amount')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'ord_invoices' AND column_name = 'amount_gross') THEN
        ALTER TABLE ord_invoices RENAME COLUMN amount TO amount_gross;
    END IF;
END $$;

ALTER TABLE ord_invoices
    ADD COLUMN IF NOT EXISTS amount_gross NUMERIC(14,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS amount_net   NUMERIC(14,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS vat_rate     INT           NOT NULL DEFAULT 22;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ord_invoices_vat_rate_chk') THEN
        ALTER TABLE ord_invoices ADD CONSTRAINT ord_invoices_vat_rate_chk CHECK (vat_rate BETWEEN 0 AND 100);
    END IF;
END $$;

-- Бэкфилл: акты без НДС — net = gross; с НДС — выделяем по ставке.
UPDATE ord_invoices
   SET vat_rate = CASE WHEN is_vat THEN vat_rate ELSE 0 END,
       amount_net = CASE WHEN is_vat THEN round(amount_gross / (1 + vat_rate / 100.0), 2) ELSE amount_gross END
 WHERE amount_net = 0 AND amount_gross <> 0;

-- Изначальный договор строки акта (по нему креатив привязывается к акту в ОРД).
-- FK DEFERRABLE: при каскадном удалении владельца (users → ord_contracts и
-- users → ord_invoices) проверка выполняется на commit, когда акт уже удалён.
ALTER TABLE ord_invoice_items
    ADD COLUMN IF NOT EXISTS contract_id UUID REFERENCES ord_contracts(id) DEFERRABLE INITIALLY DEFERRED;
UPDATE ord_invoice_items it SET contract_id = i.contract_id
  FROM ord_invoices i WHERE i.id = it.invoice_id AND it.contract_id IS NULL;

-- Разбивка суммы акта по изначальным договорам (contracts[] в v2/invoice).
CREATE TABLE IF NOT EXISTS ord_invoice_contracts (
    invoice_id          UUID NOT NULL REFERENCES ord_invoices(id) ON DELETE CASCADE,
    initial_contract_id UUID NOT NULL REFERENCES ord_contracts(id) DEFERRABLE INITIALLY DEFERRED,
    amount_gross        NUMERIC(14,2) NOT NULL DEFAULT 0,
    amount_net          NUMERIC(14,2) NOT NULL DEFAULT 0,
    vat_rate            INT NOT NULL DEFAULT 22,
    PRIMARY KEY (invoice_id, initial_contract_id)
);

-- Существующие акты (B13b) были по одному договору: он же и «изначальный».
INSERT INTO ord_invoice_contracts (invoice_id, initial_contract_id, amount_gross, amount_net, vat_rate)
SELECT i.id, i.contract_id, i.amount_gross, i.amount_net, i.vat_rate
  FROM ord_invoices i
 WHERE NOT EXISTS (SELECT 1 FROM ord_invoice_contracts ic WHERE ic.invoice_id = i.id)
ON CONFLICT DO NOTHING;
