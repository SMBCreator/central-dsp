from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from backend.database import get_db
from backend import models, auth

router = APIRouter(prefix="/pixels", tags=["pixels"])

class PixelIn(BaseModel):
    campaign_id: int
    name: str

@router.get("")
def list_pixels(campaign_id: int = None, db: Session = Depends(get_db), current_user=Depends(auth.get_current_user)):
    q = db.query(models.Pixel)
    if campaign_id:
        q = q.filter(models.Pixel.campaign_id == campaign_id)
    return q.all()

@router.post("", status_code=201)
def create_pixel(body: PixelIn, db: Session = Depends(get_db), current_user=Depends(auth.ensure_manager_or_admin)):
    c = db.query(models.Campaign).filter(models.Campaign.id == body.campaign_id).first()
    if not c:
        raise HTTPException(404, "Campaign not found")
    pixel = models.Pixel(campaign_id=body.campaign_id, name=body.name)
    db.add(pixel)
    db.commit()
    db.refresh(pixel)
    return {"id": pixel.id, "pixel_id": str(pixel.pixel_id)}

@router.delete("/{pixel_id}", status_code=204)
def delete_pixel(pixel_id: int, db: Session = Depends(get_db), current_user=Depends(auth.ensure_manager_or_admin)):
    pixel = db.query(models.Pixel).filter(models.Pixel.id == pixel_id).first()
    if not pixel:
        raise HTTPException(404, "Pixel not found")
    db.delete(pixel)
    db.commit()
