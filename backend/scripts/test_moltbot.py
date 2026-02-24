#!/usr/bin/env python3
"""Test login + Moltbot chat for all 3 tenant instances."""
import json
import urllib.request
import urllib.error

users = [
    {"api": "http://localhost:9906",  "email": "denis@0711.io", "password": "Stuttgart0711!", "name": "Denis"},
    {"api": "http://localhost:10006", "email": "alex@0711.io",  "password": "Vault2026Sicher!", "name": "Alex"},
    {"api": "http://localhost:10106", "email": "c@0711.io",     "password": "OpenClaw0711!", "name": "C"},
]

for u in users:
    print("=" * 50)
    print("  %s (%s)" % (u["name"], u["api"]))
    print("=" * 50)

    # Login
    login_data = json.dumps({"email": u["email"], "password": u["password"]}).encode()
    req = urllib.request.Request(
        u["api"] + "/auth/login",
        data=login_data,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        token = result.get("access_token", "") or result.get("token", "")
        print("  Login: OK (token: %s...)" % token[:15])
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print("  Login: FAILED - HTTP %d: %s" % (e.code, body[:100]))
        continue
    except Exception as e:
        print("  Login: FAILED - %s" % e)
        continue

    # Chat with Moltbot
    chat_data = json.dumps({"message": "Hallo Moltbot!", "include_context": False}).encode()
    req = urllib.request.Request(
        u["api"] + "/assistant/chat",
        data=chat_data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read())
        response_text = result.get("response", "")
        print("  Moltbot: %s..." % response_text[:120])
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print("  Moltbot: FAILED - HTTP %d: %s" % (e.code, body[:100]))
    except Exception as e:
        print("  Moltbot: FAILED - %s" % e)

    print()
