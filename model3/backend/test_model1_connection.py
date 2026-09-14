"""
test_model1_connection.py
Standalone script to verify Model 3's Model 1 service-account login
actually works — independent of starting the full FastAPI app, so you can
debug the auth setup directly.

Usage:
    python test_model1_connection.py

Reads the same .env as the main app (MODEL1_REGISTRY_BASE,
MODEL1_SERVICE_EMAIL, MODEL1_SERVICE_PASSWORD).
"""
import asyncio
import sys

from dotenv import load_dotenv
load_dotenv()

import model1_auth


async def main():
    print("=== Model 3 -> Model 1 connection test ===\n")

    print(f"MODEL1_REGISTRY_BASE = {model1_auth.MODEL1_REGISTRY_BASE}")
    print(f"MODEL1_SERVICE_EMAIL = {model1_auth.MODEL1_SERVICE_EMAIL or '(not set)'}")
    print(f"MODEL1_SERVICE_PASSWORD = {'*' * len(model1_auth.MODEL1_SERVICE_PASSWORD) if model1_auth.MODEL1_SERVICE_PASSWORD else '(not set)'}")
    print()

    warnings = model1_auth.config_warnings()
    if warnings:
        print("CONFIG WARNINGS:")
        for w in warnings:
            print(f"  - {w}")
        print()

    print("Attempting login...")
    try:
        token = await model1_auth.get_model1_token()
    except model1_auth.Model1AuthError as exc:
        print(f"\n❌ FAILED: {exc}")
        sys.exit(1)

    print(f"✅ SUCCESS — got a token ({len(token)} chars)")
    print()

    # Also verify the token actually works against a real Model 1 endpoint,
    # not just that login itself succeeded.
    import httpx
    print("Verifying token against Model 1's /auth/me...")
    async with httpx.AsyncClient(timeout=5) as client:
        res = await client.get(
            f"{model1_auth.MODEL1_REGISTRY_BASE}/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    if res.status_code == 200:
        me = res.json()
        print(f"✅ Token is valid. Logged in as: {me.get('email')} "
              f"(department={me.get('department')}, role={me.get('role')})")
        if me.get("department") != "Admin" and me.get("role") != "admin":
            print(
                "\n⚠️  This account is NOT department=Admin or role=admin. "
                "It will only see cameras in its own department, which likely "
                "isn't what you want for a federation service account."
            )
    else:
        print(f"❌ Token was issued but /auth/me returned {res.status_code}: {res.text[:200]}")
        sys.exit(1)

    print("\nAll checks passed — Model 3 can authenticate to Model 1 correctly.")


if __name__ == "__main__":
    asyncio.run(main())
