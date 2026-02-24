#!/usr/bin/env python3
"""Register users on Denis, Alex, C vault instances."""
import bcrypt, secrets, base64, json, urllib.request, urllib.error

def register(api_url, email, password):
    salt = base64.b64encode(secrets.token_bytes(32)).decode()
    auth_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()
    encrypted_master_key = base64.b64encode(secrets.token_bytes(64)).decode()

    payload = json.dumps({
        "email": email,
        "auth_hash": auth_hash,
        "salt": salt,
        "encrypted_master_key": encrypted_master_key,
    }).encode()

    req = urllib.request.Request(
        api_url + "/auth/register",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        return True, result
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        return False, "HTTP %d: %s" % (e.code, body)
    except Exception as e:
        return False, str(e)

users = [
    {"api": "http://localhost:9906",  "email": "denis@0711.io", "password": "Stuttgart0711!", "name": "Denis"},
    {"api": "http://localhost:10006", "email": "alex@0711.io",  "password": "Vault2026Sicher!", "name": "Alex"},
    {"api": "http://localhost:10106", "email": "c@0711.io",     "password": "OpenClaw0711!", "name": "C"},
]

print("=" * 60)
print("  0711 Vault - User Registrierung")
print("=" * 60)
print()

for u in users:
    name = u["name"]
    email = u["email"]
    api = u["api"]
    pw = u["password"]
    ok, result = register(api, email, pw)
    if ok:
        print("  [OK]  %-8s  %-20s  ->  %s" % (name, email, api))
    else:
        print("  [ERR] %-8s  %-20s  ->  %s" % (name, email, result))

print()
print("=" * 60)
print("  Zugangsdaten")
print("=" * 60)
print()
for u in users:
    print("  %s:" % u["name"])
    print("    Email:    %s" % u["email"])
    print("    Passwort: %s" % u["password"])
    print()
