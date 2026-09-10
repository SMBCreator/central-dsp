"""Campaign groups (ad groups): several campaigns united under one group."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import current_user
from rbac import require_campaign_write, scope_for
from db import get_pool

router = APIRouter(prefix="/groups", tags=["groups"])


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.get("")
async def list_groups(user: dict = Depends(current_user)):
    pool = await get_pool()
    scope = await scope_for(user)
    rows = await pool.fetch(
        f"""SELECT g.id, g.name, g.created_at, count(c.id) AS campaigns_count,
                  COALESCE(SUM(c.spent_total), 0) AS spent_total
           FROM campaign_groups g
           LEFT JOIN campaigns c ON c.group_id = g.id
           WHERE {scope.where("g.user_id", 1)}
           GROUP BY g.id ORDER BY g.created_at DESC""",
        *scope.params,
    )
    return [
        {
            "id": str(r["id"]), "name": r["name"], "created_at": r["created_at"].isoformat(),
            "campaigns_count": int(r["campaigns_count"]), "spent_total": float(r["spent_total"]),
        }
        for r in rows
    ]


@router.post("", status_code=201)
async def create_group(body: GroupIn, user: dict = Depends(require_campaign_write)):
    pool = await get_pool()
    row = await pool.fetchrow(
        "INSERT INTO campaign_groups (user_id, name) VALUES ($1::uuid, $2) RETURNING id, name, created_at",
        str(user["id"]), body.name,
    )
    return {"id": str(row["id"]), "name": row["name"], "created_at": row["created_at"].isoformat()}


@router.patch("/{group_id}")
async def rename_group(group_id: str, body: GroupIn, user: dict = Depends(require_campaign_write)):
    pool = await get_pool()
    scope = await scope_for(user)
    row = await pool.fetchrow(
        f"UPDATE campaign_groups SET name = $4 WHERE id = $1::uuid AND {scope.where('user_id', 2)} RETURNING id, name",
        group_id, *scope.params, body.name,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    return {"id": str(row["id"]), "name": row["name"]}


@router.delete("/{group_id}", status_code=204)
async def delete_group(group_id: str, user: dict = Depends(require_campaign_write)):
    """Delete the group; campaigns keep running (group_id becomes NULL)."""
    pool = await get_pool()
    scope = await scope_for(user)
    result = await pool.execute(
        f"DELETE FROM campaign_groups WHERE id = $1::uuid AND {scope.where('user_id', 2)}", group_id, *scope.params
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Группа не найдена")
