"""HTTP-гигиена ответов API (audit O-07 / C6).

Cache-Control: no-store для всех ответов API, у которых роутер сам не выставил
политику кеширования. Ответы кабинета персональные (баланс, кампании, /auth/me);
без явного заголовка браузер и промежуточные прокси могут положить их в кеш и
показать после logout или другому пользователю за тем же прокси.

Исключения — публичные/технические пути, где кеш допустим или заголовок
управляется отдельно: статика креативов /uploads (отдаётся SSP/браузерам с
рекламой), пиксель, health/ready/metrics и RTB hot path (у SSP свой контракт,
лишний заголовок там не нужен).
"""

NO_STORE = "no-store"

_EXEMPT_PREFIXES = ("/uploads", "/pixel", "/health", "/ready", "/metrics", "/rtb", "/rtb-feed")


def is_exempt(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _EXEMPT_PREFIXES)


class NoStoreMiddleware:
    """Чистый ASGI: добавляет Cache-Control: no-store в http-ответы, если его нет.
    Работает и для ответов из мидлварей ниже (429/403/404) — заголовок ставится
    на этапе http.response.start. Не покрывает только 500 от необработанного
    исключения: его формирует ServerErrorMiddleware снаружи всех user-middleware,
    тело там константное, без персональных данных."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or is_exempt(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                if not any(k.lower() == b"cache-control" for k, _ in headers):
                    headers.append((b"cache-control", NO_STORE.encode()))
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)
