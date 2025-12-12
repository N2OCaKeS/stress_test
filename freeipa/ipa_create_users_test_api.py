#!/usr/bin/env python3
import asyncio
import httpx
import time

SERVER = "lowserver.stress-testing.local"
ADMIN_PASS = "12345678"

async def main():
    start = time.perf_counter()
    async with httpx.AsyncClient(verify=False, timeout=30) as c:
        c.headers["referer"] = f"https://{SERVER}/ipa"

        await c.post(f"https://{SERVER}/ipa/session/login_password",
                     data={"user": "admin", "password": ADMIN_PASS})

        async def add(id):
            login = f"user{id}"
            await c.post(
                f"https://{SERVER}/ipa/session/json",
                json={
                    "method": "user_add",
                    "params": [[login], {
                        "givenname": "Test",
                        "sn": "Тестов",
                        "userpassword": "123456",
                        "random": False
                    }],
                    "id": id
                }
            )
            print(login)

        await asyncio.gather(*(add(i) for i in range(1, 101)))
    total = time.perf_counter() - start
    print(f"\n{total}")
asyncio.run(main())