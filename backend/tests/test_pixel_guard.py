"""Дедуп и антифлуд пикселя (pixel_guard) на fakeredis."""
import asyncio
import unittest

import fakeredis.aioredis

import cache
import pixel_guard


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class PixelGuardTest(unittest.TestCase):
    def setUp(self):
        self._orig = cache._client
        cache._client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._limits = (pixel_guard.SESSION_RPM, pixel_guard.IP_RPM)

    def tearDown(self):
        cache._client = self._orig
        pixel_guard.SESSION_RPM, pixel_guard.IP_RPM = self._limits

    def _ev(self, **kw):
        base = dict(pixel_id="p1", session_id="s1", event_type="pageview",
                    event_name=None, url="https://a.ru/", client_ip="1.2.3.4")
        base.update(kw)
        return pixel_guard.should_accept(**base)

    def test_duplicate_within_window_dropped(self):
        async def go():
            self.assertIsNone(await self._ev())
            self.assertEqual(await self._ev(), pixel_guard.DROP_DUPLICATE)
            # другой URL — уже не дубль
            self.assertIsNone(await self._ev(url="https://a.ru/b"))
            # другой тип — не дубль
            self.assertIsNone(await self._ev(event_type="event", event_name="buy"))
        run(go())

    def test_heartbeat_has_own_window(self):
        async def go():
            r = cache.get_redis()
            self.assertIsNone(await self._ev(event_type="heartbeat"))
            keys = await r.keys("px:dd:*")
            ttl = await r.ttl(keys[0])
            self.assertGreaterEqual(ttl, 9)
        run(go())

    def test_session_limit(self):
        pixel_guard.SESSION_RPM = 3
        pixel_guard.IP_RPM = 10_000

        async def go():
            for i in range(3):
                self.assertIsNone(await self._ev(url=f"https://a.ru/{i}"))
            self.assertEqual(await self._ev(url="https://a.ru/x"), pixel_guard.DROP_SESSION_LIMIT)
            # другая сессия с того же IP — проходит
            self.assertIsNone(await self._ev(session_id="s2"))
        run(go())

    def test_ip_limit_beats_new_sessions(self):
        pixel_guard.SESSION_RPM = 10_000
        pixel_guard.IP_RPM = 2

        async def go():
            self.assertIsNone(await self._ev(session_id="a"))
            self.assertIsNone(await self._ev(session_id="b"))
            self.assertEqual(await self._ev(session_id="c"), pixel_guard.DROP_IP_LIMIT)
            # без IP лимит не применяется
            self.assertIsNone(await self._ev(session_id="d", client_ip=None))
        run(go())

    def test_fail_open_without_redis(self):
        cache._client = None
        import os
        saved = {k: os.environ.pop(k, None) for k in ("REDIS_URL", "KV_URL")}
        try:
            self.assertIsNone(run(self._ev()))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
