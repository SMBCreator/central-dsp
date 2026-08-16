from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from backend.database import get_db
from backend import models, auth

router = APIRouter(prefix="/auth", tags=["auth"])

class RegisterIn(BaseModel):
    email: EmailStr
    password: str
    full_name: str = ""

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    email: str
    role: str

@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if db.query(models.User).filter(models.User.email == body.email).first():
        raise HTTPException(400, "Email already registered")
    role = "admin" if db.query(models.User).count() == 0 else "viewer"
    user = models.User(
        email=body.email,
        password_hash=auth.hash_password(body.password),
        full_name=body.full_name,
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = auth.create_access_token({"sub": str(user.id)})
    return TokenOut(access_token=token, user_id=user.id, email=user.email, role=user.role)

@router.post("/login", response_model=TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == form.username).first()
    if not user or not auth.verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    token = auth.create_access_token({"sub": str(user.id)})
    return TokenOut(access_token=token, user_id=user.id, email=user.email, role=user.role)

@router.get("/me")
def me(current_user: models.User = Depends(auth.get_current_user)):
    return {"id": current_user.id, "email": current_user.email, "full_name": current_user.full_name, "role": current_user.role}
