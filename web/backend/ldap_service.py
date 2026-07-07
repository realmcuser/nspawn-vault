import ssl
from typing import Optional
import models


def ldap_authenticate(username: str, password: str, cfg: "models.LdapSettings") -> Optional[dict]:
    """Bind to LDAP and return {is_admin: bool} on success, None on failure."""
    try:
        from ldap3 import Server, Connection, ALL, SUBTREE, Tls
        tls_config = Tls(validate=ssl.CERT_NONE) if not cfg.tls_verify else None
        server = Server(cfg.server_url, tls=tls_config, get_info=ALL, connect_timeout=5)

        if cfg.bind_dn_template:
            user_bind_dn = cfg.bind_dn_template.replace("{username}", username)
        else:
            user_bind_dn = f"{cfg.user_attr}={username},{cfg.base_dn}"

        try:
            conn = Connection(server, user=user_bind_dn, password=password, auto_bind=True)
        except Exception as e:
            print(f"[LDAP] bind failed for user_bind_dn='{user_bind_dn}': {e}", flush=True)
            return None

        is_admin = False
        if cfg.required_group_dn or cfg.admin_group_dn:
            # Use the service account for the group lookup if configured — the
            # logged-in user's own bind often lacks permission to read memberOf.
            search_conn = conn
            if cfg.bind_dn and cfg.bind_password:
                try:
                    search_conn = Connection(server, user=cfg.bind_dn, password=cfg.bind_password, auto_bind=True)
                except Exception as e:
                    print(f"[LDAP] service account bind failed for '{cfg.bind_dn}': {e}", flush=True)
                    conn.unbind()
                    return None

            search_conn.search(
                search_base=cfg.base_dn,
                search_filter=f"({cfg.user_attr}={username})",
                search_scope=SUBTREE,
                attributes=["memberOf"],
            )
            if not search_conn.entries:
                print(f"[LDAP] group lookup found no entry for '{username}' under base_dn='{cfg.base_dn}'", flush=True)
                if search_conn is not conn:
                    search_conn.unbind()
                conn.unbind()
                return None
            entry = search_conn.entries[0]
            member_of = [m.lower() for m in (entry.memberOf.values if entry.memberOf else [])]
            if search_conn is not conn:
                search_conn.unbind()

            if cfg.required_group_dn and cfg.required_group_dn.lower() not in member_of:
                print(f"[LDAP] user '{username}' is not a member of required_group_dn='{cfg.required_group_dn}' (memberOf={member_of})", flush=True)
                conn.unbind()
                return None
            if cfg.admin_group_dn:
                is_admin = cfg.admin_group_dn.lower() in member_of

        conn.unbind()
        return {"is_admin": is_admin}
    except Exception as e:
        print(f"[LDAP] authentication error for user '{username}': {e}", flush=True)
        return None


def ldap_test_connection(server_url: str, bind_dn: Optional[str], bind_password: Optional[str], tls_verify: bool) -> dict:
    """Test LDAP server reachability. Returns {success, message}."""
    try:
        from ldap3 import Server, Connection, ALL, Tls
        tls_config = Tls(validate=ssl.CERT_NONE) if not tls_verify else None
        server = Server(server_url, tls=tls_config, get_info=ALL, connect_timeout=5)
        if bind_dn and bind_password:
            conn = Connection(server, user=bind_dn, password=bind_password, auto_bind=True)
            msg = f"Connected and authenticated as {bind_dn}"
        else:
            conn = Connection(server, auto_bind=True)
            msg = "Connected to LDAP server (anonymous bind)"
        conn.unbind()
        return {"success": True, "message": msg}
    except Exception as e:
        return {"success": False, "message": str(e)}
