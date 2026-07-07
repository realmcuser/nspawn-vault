from sqlalchemy import Column, Integer, String, Boolean
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    role = Column(String, default="user")  # admin, user
    auth_source = Column(String, default="local")  # local, ldap


class LdapSettings(Base):
    __tablename__ = "ldap_settings"

    id = Column(Integer, primary_key=True, default=1)
    enabled = Column(Boolean, default=False)
    server_url = Column(String, nullable=True)
    base_dn = Column(String, nullable=True)
    user_attr = Column(String, default="uid")
    bind_dn_template = Column(String, nullable=True)  # e.g. uid={username},cn=users,cn=accounts,dc=...
    bind_dn = Column(String, nullable=True)            # optional service account for group lookups
    bind_password = Column(String, nullable=True)
    required_group_dn = Column(String, nullable=True)
    admin_group_dn = Column(String, nullable=True)
    tls_verify = Column(Boolean, default=True)


class SystemSettings(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(String, nullable=True)


class AuditLog(Base):
    """Records every access to actual customer/container data through this
    app (whole-container downloads, single-file downloads, and directory
    browsing) - added once the file browser/download feature made this the
    first place the UI exposes real backed-up content directly, rather than
    just status/config. Never records file *contents*, only which action
    was taken against which host/container/snapshot/path, by whom, when."""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(String, nullable=False, index=True)  # ISO-8601, UTC
    username = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False)  # download_archive, download_file, browse
    host = Column(String, nullable=False)
    container = Column(String, nullable=False)
    snapshot = Column(String, nullable=True)
    path = Column(String, nullable=True)
    detail = Column(String, nullable=True)  # e.g. compression method
    client_ip = Column(String, nullable=True)
