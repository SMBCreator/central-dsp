-- GAP-UI-002: supply_partners.auction_type был TEXT ('first_price'),
-- а API/фронт/OpenRTB используют INT (1 = first-price, 2 = second-price).
-- Приводим к OpenRTB-канону (поле `at` в BidRequest).
ALTER TABLE supply_partners
    ALTER COLUMN auction_type DROP DEFAULT;

ALTER TABLE supply_partners
    ALTER COLUMN auction_type TYPE SMALLINT
    USING (
        CASE auction_type
            WHEN 'first_price'  THEN 1
            WHEN 'second_price' THEN 2
            ELSE 2
        END
    );

ALTER TABLE supply_partners
    ALTER COLUMN auction_type SET DEFAULT 2;

ALTER TABLE supply_partners
    ADD CONSTRAINT supply_partners_auction_type_check CHECK (auction_type IN (1, 2));
