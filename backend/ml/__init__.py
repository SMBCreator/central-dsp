"""ML-слой CentralDSP (Часть B).

Модули:
  pctr    — сегментная pCTR-модель (Beta-смузинг + лог-odds), обучение кроном,
            онлайн-скоринг из in-process кэша со строгим fallback;
  quality — скоринг качества доменов (explainable-правила), clean-traffic режим.

Принципы (skill: Optimization and ML):
  * никакого удалённого inference в hot path — только кэш в памяти процесса;
  * отсутствие модели/кэша => None => биддер работает по базовой цене (fail-safe);
  * модель версионируется в ml_models, decision log в bid_events.pctr;
  * guardrails на корректировку бида задаются вне модели (rtb.py).

Почему не CatBoost (из исходного плана): бинарник 100+ МБ несовместим с
serverless-развёртыванием backend'а и бюджетом латентности hot path.
Сегментная Beta-модель прозрачна, обучается за секунды SQL-агрегатом и
даёт объяснимый score. Переход на GBDT возможен позже отдельной версией
model_type='pctr_gbdt' без изменения контракта predict().
"""
