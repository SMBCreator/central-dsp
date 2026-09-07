"""E-05: атрибуция конверсии пикселя по clid — только свой бид и только в окне.

Раньше _match_goals брал первый bid_events по bid_id без привязки к владельцу
пикселя: чужой clid (подсмотренный, перебранный, из другого аккаунта) вешал
конверсию на чужую кампанию и портил ей CPA. Теперь:
- бид кампании владельца пикселя (или клиента его агентства) — атрибутируется;
- бид чужого пользователя — конверсия organic (campaign_id NULL), hourly не растёт;
- свой, но старше окна атрибуции — тоже organic.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest

from routers import pixel as pixel_module

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


async def _user(conn, role="advertiser", parent=None):
    return await conn.fetchval(
        """INSERT INTO users (email, password_hash, name, role, parent_user_id)
           VALUES ($1, 'x', 'e05', $2, $3) RETURNING id""",
        f"e05-{uuid.uuid4().hex[:8]}@test.local", role, parent,
    )


async def _campaign(conn, user_id):
    adv = await conn.fetchval("INSERT INTO advertisers (user_id, name) VALUES ($1, 'e05') RETURNING id", user_id)
    return await conn.fetchval(
        "INSERT INTO campaigns (advertiser_id, user_id, name) VALUES ($1, $2, 'e05') RETURNING id", adv, user_id
    )


async def _bid(conn, camp, *, age_days=0):
    bid_id = f"e05-{uuid.uuid4().hex}"
    await conn.execute(
        """INSERT INTO bid_events (event_type, bid_id, campaign_id, domain, device_type, geo_country, created_at)
           VALUES ('bid', $1, $2, 'site.ru', 'desktop', 'RU', now() - make_interval(days => $3))""",
        bid_id, camp, age_days,
    )
    return bid_id


async def _conv(conn, goal_id, sess):
    return await conn.fetchrow(
        "SELECT campaign_id FROM conversions WHERE goal_id = $1 AND session_id = $2", goal_id, sess
    )


def test_clid_ownership_and_window():
    async def main():
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        users: list = []
        camps: list = []
        pixel_id = goal_id = None
        try:
            async with pool.acquire() as conn:
                agency = await _user(conn, role="agency")
                owner = await _user(conn, parent=agency)
                stranger = await _user(conn)
                users += [owner, stranger, agency]
                own_camp = await _campaign(conn, owner)
                agency_camp = await _campaign(conn, agency)  # родитель владельца — НЕ потомок
                sibling = await _user(conn, parent=agency)
                users.append(sibling)
                sibling_camp = await _campaign(conn, sibling)
                stranger_camp = await _campaign(conn, stranger)
                camps += [own_camp, agency_camp, sibling_camp, stranger_camp]

                pixel_id = await conn.fetchval(
                    "INSERT INTO pixels (user_id, name, domain) VALUES ($1, 'e05', 'site.ru') RETURNING id", owner
                )
                goal_id = await conn.fetchval(
                    """INSERT INTO goals (pixel_id, name, goal_type, match_value, value)
                       VALUES ($1, 'lead', 'event', 'lead', 10) RETURNING id""",
                    pixel_id,
                )
                own_bid = await _bid(conn, own_camp)
                old_own_bid = await _bid(conn, own_camp, age_days=pixel_module.ATTRIBUTION_WINDOW_DAYS + 1)
                stranger_bid = await _bid(conn, stranger_camp)
                sibling_bid = await _bid(conn, sibling_camp)

            async def hit(sess, clid):
                await pixel_module._match_goals(
                    pool, str(pixel_id), str(owner), sess, "event", "lead", "https://site.ru/ok", clid
                )

            await hit("s-own", own_bid)
            await hit("s-old", old_own_bid)
            await hit("s-stranger", stranger_bid)
            await hit("s-sibling", sibling_bid)
            await hit("s-garbage", "no-such-bid")

            async with pool.acquire() as conn:
                assert (await _conv(conn, goal_id, "s-own"))["campaign_id"] == own_camp
                assert (await _conv(conn, goal_id, "s-old"))["campaign_id"] is None, "вне окна атрибуции → organic"
                assert (await _conv(conn, goal_id, "s-stranger"))["campaign_id"] is None, "чужой clid → organic"
                assert (await _conv(conn, goal_id, "s-sibling"))["campaign_id"] is None, \
                    "клиент того же агентства — не владелец пикселя и не его потомок"
                assert (await _conv(conn, goal_id, "s-garbage"))["campaign_id"] is None
                # hourly-конверсии выросли только у своей кампании
                for c, expect in ((own_camp, 1), (stranger_camp, 0), (sibling_camp, 0)):
                    n = await conn.fetchval(
                        "SELECT coalesce(sum(conversions),0) FROM stats_hourly WHERE campaign_id = $1", c
                    )
                    assert int(n) == expect, (c, n)

            # Пиксель агентства видит клики своих клиентов (поддерево вниз).
            async with pool.acquire() as conn:
                ag_pixel = await conn.fetchval(
                    "INSERT INTO pixels (user_id, name, domain) VALUES ($1, 'e05-ag', 'site.ru') RETURNING id", agency
                )
                ag_goal = await conn.fetchval(
                    """INSERT INTO goals (pixel_id, name, goal_type, match_value, value)
                       VALUES ($1, 'lead', 'event', 'lead', 0) RETURNING id""",
                    ag_pixel,
                )
            try:
                await pixel_module._match_goals(
                    pool, str(ag_pixel), str(agency), "s-ag", "event", "lead", None, own_bid
                )
                async with pool.acquire() as conn:
                    assert (await _conv(conn, ag_goal, "s-ag"))["campaign_id"] == own_camp
            finally:
                async with pool.acquire() as conn:
                    await conn.execute("DELETE FROM conversions WHERE goal_id = $1", ag_goal)
                    await conn.execute("DELETE FROM goals WHERE id = $1", ag_goal)
                    await conn.execute("DELETE FROM pixels WHERE id = $1", ag_pixel)
        finally:
            async with pool.acquire() as conn:
                for c in camps:
                    await conn.execute("DELETE FROM bid_events WHERE campaign_id = $1", c)
                    await conn.execute("DELETE FROM stats_hourly WHERE campaign_id = $1", c)
                if goal_id:
                    await conn.execute("DELETE FROM conversions WHERE goal_id = $1", goal_id)
                    await conn.execute("DELETE FROM goals WHERE id = $1", goal_id)
                if pixel_id:
                    await conn.execute("DELETE FROM pixels WHERE id = $1", pixel_id)
                # users каскадом удаляют advertisers/campaigns; детей — раньше родителя
                for u in reversed(users):
                    await conn.execute("DELETE FROM users WHERE id = $1", u)
            await pool.close()

    asyncio.run(main())
