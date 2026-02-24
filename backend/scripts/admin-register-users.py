#!/usr/bin/env python3
"""
Admin user provisioning for 0711 Vault tenants (Denis, Alex, C).

Registriert User ueber die vault-api /auth/register Endpoints.
Zero-Knowledge Crypto: auth_hash wird per bcrypt erzeugt,
encrypted_master_key ist ein Dummy (User re-encrypts on first login).

Usage:
    python3 admin-register-users.py

WICHTIG: Passwoerter vor dem Ausfuehren setzen!
"""
import bcrypt
import secrets
import base64
import json
import sys
import urllib.request
import urllib.error


def register_user(api_url, email, password, display_name=None):
    """Register a user on a vault-api instance."""
    # Generate salt for key derivation
    salt = base64.b64encode(secrets.token_bytes(32)).decode()

    # Compute auth_hash (bcrypt of password — matching frontend logic)
    auth_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()

    # Generate encrypted master key (dummy — user re-encrypts on first login)
    encrypted_master_key = base64.b64encode(secrets.token_bytes(64)).decode()

    payload = json.dumps({
        "email": email,
        "auth_hash": auth_hash,
        "salt": salt,
        "encrypted_master_key": encrypted_master_key,
    }).encode()

    req = urllib.request.Request(
        f"{api_url}/auth/register",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        print(f"  [OK] {email} registriert: {result}")
        return result
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"  [FEHLER] {email}: HTTP {e.code} — {body}")
        return None
    except urllib.error.URLError as e:
        print(f"  [FEHLER] {email}: Verbindung fehlgeschlagen — {e.reason}")
        return None


def main():
    # =========================================================================
    # TENANT-KONFIGURATION
    # WICHTIG: Passwoerter vor dem Ausfuehren aendern!
    # =========================================================================
    tenants = {
        "denis": {
            "port": 9906,
            "email": "denis@0711.io",
            "password": "CHANGEME",  # <-- AENDERN!
        },
        "alex": {
            "port": 10006,
            "email": "alex@0711.io",
            "password": "CHANGEME",  # <-- AENDERN!
        },
        "c": {
            "port": 10106,
            "email": "c@0711.io",
            "password": "CHANGEME",  # <-- AENDERN!
        },
    }

    # Pruefen ob Passwoerter gesetzt wurden
    unchanged = [name for name, cfg in tenants.items() if cfg["password"] == "CHANGEME"]
    if unchanged:
        print("=" * 60)
        print("WARNUNG: Passwoerter noch nicht gesetzt!")
        print(f"Betroffen: {', '.join(unchanged)}")
        print("")
        print("Bitte dieses Script editieren und Passwoerter setzen.")
        print("=" * 60)
        resp = input("Trotzdem fortfahren mit 'CHANGEME'? (y/N) ")
        if resp.strip().lower() != "y":
            print("Abgebrochen.")
            sys.exit(1)

    print("")
    print("=" * 60)
    print("  0711 Vault — User Registrierung")
    print("=" * 60)
    print("")

    results = {}
    for name, cfg in tenants.items():
        api_url = f"http://localhost:{cfg['port']}"
        print(f"[{name}] Registriere {cfg['email']} auf {api_url}...")
        result = register_user(api_url, cfg["email"], cfg["password"])
        results[name] = result
        print("")

    # Zusammenfassung
    print("=" * 60)
    print("  Zusammenfassung")
    print("=" * 60)
    for name, result in results.items():
        status = "OK" if result else "FEHLER"
        print(f"  {name}: {status}")
    print("")
    print("User koennen ihr Passwort danach in der UI aendern.")


if __name__ == "__main__":
    main()
