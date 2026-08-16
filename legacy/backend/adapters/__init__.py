"""
CentralDSP Ad Network Adapters.

Паттерн: каждый адаптер наследует CentralAdNetworkAdapter из base.py
и реализует унифицированный интерфейс управления кампаниями и получения статистики.

Поддерживаемые сети:
  push_house   — Push.House
  propellerads — PropellerAds
  mgid         — MGID (native)
  evadav       — EvaDav (push/pop/native)
  rtb          — внутренний RTB4FREE биддер (управляется через rtb_bridge)
"""
from .base import CentralAdNetworkAdapter
from .push_house import PushHouseAdapter
from .propellerads import PropellerAdsAdapter
from .mgid import MGIDAdapter
from .evadav import EvaDavAdapter

# Реестр: network_slug → класс адаптера
# Используется в tasks/sync.py и routers/networks.py
ADAPTER_REGISTRY = {
    "push_house":   PushHouseAdapter,
    "propellerads": PropellerAdsAdapter,
    "mgid":         MGIDAdapter,
    "evadav":       EvaDavAdapter,
}


def get_adapter(network: str, credentials: dict) -> CentralAdNetworkAdapter:
    """
    Фабричная функция. Создаёт экземпляр адаптера по slug сети.

    credentials для каждой сети:
      push_house:   {"api_token": "..."}
      propellerads: {"api_key": "..."}
      mgid:         {"client_id": "...", "token": "..."}
      evadav:       {"api_token": "..."}
    """
    cls = ADAPTER_REGISTRY.get(network)
    if cls is None:
        raise ValueError(f"Unknown ad network: '{network}'. Available: {list(ADAPTER_REGISTRY.keys())}")

    if network == "push_house":
        return cls(api_token=credentials["api_token"])
    elif network == "propellerads":
        return cls(api_key=credentials["api_key"])
    elif network == "mgid":
        return cls(client_id=credentials["client_id"], token=credentials["token"])
    elif network == "evadav":
        return cls(api_token=credentials["api_token"])
    else:
        raise ValueError(f"No factory rule for network: '{network}'")


__all__ = [
    "CentralAdNetworkAdapter",
    "PushHouseAdapter",
    "PropellerAdsAdapter",
    "MGIDAdapter",
    "EvaDavAdapter",
    "ADAPTER_REGISTRY",
    "get_adapter",
]
