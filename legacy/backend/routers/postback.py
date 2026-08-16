import uuid
from fastapi import APIRouter, Request, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from backend.database import get_db
from backend import models

router = APIRouter(prefix="/postback", tags=["postback"])

@router.get("", include_in_schema=False)
@router.post("", include_in_schema=False)
async def receive_postback(request: Request, db: Session = Depends(get_db)):
    params = dict(request.query_params)
    if request.method == "POST":
        try:
            body = await request.json()
            params.update(body)
        except Exception:
            form = await request.form()
            params.update(dict(form))

    click_id_str = params.get("click_id")
    payout = float(params.get("payout", 0) or 0)
    currency = params.get("currency", "USD")
    status = params.get("status", "approved")

    click_uuid = None
    if click_id_str:
        try:
            click_uuid = uuid.UUID(click_id_str)
        except ValueError:
            pass

    pb = models.Postback(click_id=click_uuid, payout=payout, currency=currency, status=status, raw_params=params, ip=request.client.host)
    db.add(pb)
    db.flush()

    if click_uuid and status not in ("rejected", "fraud", "chargeback"):
        click = db.query(models.Click).filter(models.Click.click_id == click_uuid).first()
        if click:
            stat = db.query(models.Stat).filter(
                models.Stat.link_id == click.link_id,
                models.Stat.sub_id == click.sub_id,
                models.Stat.creative_id == click.creative_id,
            ).first()
            if stat:
                stat.conversions = (stat.conversions or 0) + 1
                stat.revenue = float(stat.revenue or 0) + payout
            db.add(models.Event(
                link_id=click.link_id, event_name="conversion",
                sub_id=click.sub_id, creative_id=click.creative_id,
                click_id=str(click_uuid), value=payout, currency=currency,
                ip=request.client.host, country_code=click.country_code,
                device_type=click.device_type, metadata_=params,
            ))
            pb.processed = True

    db.commit()
    return PlainTextResponse("OK")
