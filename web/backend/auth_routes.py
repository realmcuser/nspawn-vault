from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session

import models
import auth_utils
import ldap_service
from database import SessionLocal

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/token")


def _user_from_token(token: str, db: Session) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = auth_utils.jwt.decode(token, auth_utils.SECRET_KEY, algorithms=[auth_utils.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except auth_utils.JWTError:
        raise credentials_exception

    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None:
        raise credentials_exception
    return user


async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    return _user_from_token(token, db)


async def get_current_admin(current_user: models.User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


async def get_current_admin_from_query_token(token: str, db: Session = Depends(get_db)):
    """Same validation as get_current_admin, but reads the JWT from a query
    parameter instead of the Authorization header. Only used for the
    container-archive download endpoint: a native browser download (plain
    navigation to a URL) can't attach a custom header, and that's what lets
    the browser stream a large file straight to disk instead of buffering
    the whole thing in page memory first - containers have already been
    seen at 13GB+ in this project, which a JS fetch()-into-Blob approach
    would hold entirely in RAM before the user could even save it."""
    user = _user_from_token(token, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def get_system_setting(db: Session, key: str, default: str = None) -> str:
    setting = db.query(models.SystemSettings).filter(models.SystemSettings.key == key).first()
    return setting.value if setting else default


def set_system_setting(db: Session, key: str, value: str):
    setting = db.query(models.SystemSettings).filter(models.SystemSettings.key == key).first()
    if setting:
        setting.value = value
    else:
        setting = models.SystemSettings(key=key, value=value)
        db.add(setting)
    db.commit()


# --- Schemas ---

class Token(BaseModel):
    access_token: str
    token_type: str


class UserCreate(BaseModel):
    username: str
    password: str


class User(BaseModel):
    id: int
    username: str
    is_active: bool
    role: str
    auth_source: str = "local"

    class Config:
        from_attributes = True


class LdapSettingsSchema(BaseModel):
    enabled: bool = False
    server_url: Optional[str] = None
    base_dn: Optional[str] = None
    user_attr: str = "uid"
    bind_dn_template: Optional[str] = None
    bind_dn: Optional[str] = None
    bind_password: Optional[str] = None
    required_group_dn: Optional[str] = None
    admin_group_dn: Optional[str] = None
    tls_verify: bool = True


class LdapTestRequest(BaseModel):
    server_url: str
    bind_dn: Optional[str] = None
    bind_password: Optional[str] = None
    tls_verify: bool = True


class UserUpdate(BaseModel):
    is_active: Optional[bool] = None
    role: Optional[str] = None


class SystemSettingsResponse(BaseModel):
    allow_registration: bool


# --- Login / registration ---

@router.post("/api/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    _unauth = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
        headers={"WWW-Authenticate": "Bearer"},
    )
    # Always try local auth first (local users never fall through to LDAP)
    local_user = db.query(models.User).filter(
        models.User.username == form_data.username,
        models.User.auth_source == "local",
    ).first()

    if local_user:
        if not auth_utils.verify_password(form_data.password, local_user.hashed_password):
            raise _unauth
        if not local_user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is disabled", headers={"WWW-Authenticate": "Bearer"})
        user = local_user
    else:
        # No local user — try LDAP if enabled
        ldap_cfg = db.query(models.LdapSettings).filter(models.LdapSettings.id == 1).first()
        if not ldap_cfg or not ldap_cfg.enabled:
            print(f"[LDAP] login attempt for '{form_data.username}' rejected — LDAP is not enabled", flush=True)
            raise _unauth

        result = ldap_service.ldap_authenticate(form_data.username, form_data.password, ldap_cfg)
        if result is None:
            raise _unauth

        # Auto-provision or refresh LDAP user. Unlike the original auth stack
        # this was ported from, role/group membership is re-synced on EVERY login,
        # not just at first provisioning — an admin removed from the LDAP admin
        # group loses admin here immediately instead of staying admin forever.
        user = db.query(models.User).filter(
            models.User.username == form_data.username,
            models.User.auth_source == "ldap",
        ).first()
        fresh_role = "admin" if result.get("is_admin") else "user"
        if not user:
            user = models.User(
                username=form_data.username,
                hashed_password="!ldap",
                role=fresh_role,
                auth_source="ldap",
                is_active=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            if not user.is_active:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is disabled", headers={"WWW-Authenticate": "Bearer"})
            if user.role != fresh_role:
                user.role = fresh_role
                db.commit()
                db.refresh(user)

    access_token_expires = auth_utils.timedelta(minutes=auth_utils.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth_utils.create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/api/users", response_model=User)
async def create_user(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")

    user_count = db.query(models.User).count()
    is_first_user = user_count == 0

    if not is_first_user:
        allow_reg = get_system_setting(db, "allow_registration", "true")
        if allow_reg.lower() != "true":
            raise HTTPException(status_code=403, detail="Registration is disabled")

    hashed_password = auth_utils.get_password_hash(user.password)
    role = "admin" if is_first_user else "user"
    db_user = models.User(username=user.username, hashed_password=hashed_password, role=role)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


@router.get("/api/users/me", response_model=User)
async def read_users_me(current_user: models.User = Depends(get_current_user)):
    return current_user


@router.get("/api/settings/registration")
async def check_registration_allowed(db: Session = Depends(get_db)):
    user_count = db.query(models.User).count()
    if user_count == 0:
        return {"allowed": True, "first_user": True}
    allow_reg = get_system_setting(db, "allow_registration", "true")
    return {"allowed": allow_reg.lower() == "true", "first_user": False}


# --- Admin: users ---

@router.get("/api/admin/users", response_model=List[User])
async def list_users(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_admin)):
    return db.query(models.User).all()


@router.put("/api/admin/users/{user_id}", response_model=User)
async def update_user(user_id: int, user_update: UserUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_admin)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == current_user.id and user_update.is_active is False:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")

    if user.id == current_user.id and user_update.role and user_update.role != "admin":
        admin_count = db.query(models.User).filter(models.User.role == "admin").count()
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last admin")

    if user_update.is_active is not None:
        user.is_active = user_update.is_active
    if user_update.role is not None:
        user.role = user_update.role

    db.commit()
    db.refresh(user)
    return user


@router.get("/api/admin/settings", response_model=SystemSettingsResponse)
async def get_admin_settings(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_admin)):
    allow_reg = get_system_setting(db, "allow_registration", "true")
    return {"allow_registration": allow_reg.lower() == "true"}


@router.put("/api/admin/settings", response_model=SystemSettingsResponse)
async def update_admin_settings(settings: SystemSettingsResponse, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_admin)):
    set_system_setting(db, "allow_registration", str(settings.allow_registration).lower())
    return settings


# --- Admin: LDAP ---

@router.get("/api/admin/ldap", response_model=LdapSettingsSchema)
async def get_ldap_settings(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_admin)):
    """Get LDAP configuration (admin only). bind_password is redacted — the
    original auth stack this was ported from returns it in plaintext, which
    we deliberately do not replicate."""
    cfg = db.query(models.LdapSettings).filter(models.LdapSettings.id == 1).first()
    if not cfg:
        return LdapSettingsSchema()
    return LdapSettingsSchema(
        enabled=cfg.enabled,
        server_url=cfg.server_url,
        base_dn=cfg.base_dn,
        user_attr=cfg.user_attr or "uid",
        bind_dn_template=cfg.bind_dn_template,
        bind_dn=cfg.bind_dn,
        bind_password="********" if cfg.bind_password else None,
        required_group_dn=cfg.required_group_dn,
        admin_group_dn=cfg.admin_group_dn,
        tls_verify=cfg.tls_verify if cfg.tls_verify is not None else True,
    )


@router.put("/api/admin/ldap", response_model=LdapSettingsSchema)
async def update_ldap_settings(data: LdapSettingsSchema, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_admin)):
    """Update LDAP configuration (admin only). If bind_password comes back as
    the redaction sentinel "********" (i.e. the admin didn't change it in the
    form), keep the stored password instead of overwriting it with the
    sentinel string."""
    cfg = db.query(models.LdapSettings).filter(models.LdapSettings.id == 1).first()
    if not cfg:
        cfg = models.LdapSettings(id=1)
        db.add(cfg)
    cfg.enabled = data.enabled
    cfg.server_url = data.server_url
    cfg.base_dn = data.base_dn
    cfg.user_attr = data.user_attr
    cfg.bind_dn_template = data.bind_dn_template
    cfg.bind_dn = data.bind_dn
    if data.bind_password is not None and data.bind_password != "********":
        cfg.bind_password = data.bind_password
    cfg.required_group_dn = data.required_group_dn
    cfg.admin_group_dn = data.admin_group_dn
    cfg.tls_verify = data.tls_verify
    db.commit()
    db.refresh(cfg)
    return await get_ldap_settings(db=db, current_user=current_user)


@router.post("/api/admin/ldap/test")
async def test_ldap_settings(req: LdapTestRequest, current_user: models.User = Depends(get_current_admin)):
    return ldap_service.ldap_test_connection(req.server_url, req.bind_dn, req.bind_password, req.tls_verify)
