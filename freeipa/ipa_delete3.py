#!/usr/bin/env python3
import asyncio
import httpx
from tqdm.asyncio import tqdm_asyncio

SERVER = "virtual-station1.stress-testing.local"
ADMIN_PASS = "12345678"
USERS_TO_DELETE = 1000
CONCURRENT = 10

FAILED_USERS = []

async def login(client):
    resp = await client.post(
        f"https://{SERVER}/ipa/session/login_password",
        data={"user": "admin", "password": ADMIN_PASS}
    )
    return resp.status_code == 200

async def delete_user(client, user_id, semaphore):
    async with semaphore:
        login_name = f"test{user_id}"
        
        # Попробуем до 3-х раз в случае сетевых сбоев или 401 ошибки
        for attempt in range(3):
            try:
                response = await client.post(
                    f"https://{SERVER}/ipa/session/json",
                    json={
                        "method": "user_del",
                        "params": [[login_name], {"continue": True}],
                        "id": user_id
                    }
                )

                if response.status_code == 401:
                    if await login(client):
                        continue
                    else:
                        FAILED_USERS.append(f"{login_name}: Re-login failed (401)")
                        return

                if response.status_code != 200:
                    FAILED_USERS.append(f"{login_name}: HTTP {response.status_code}")
                    return

                data = response.json()
                if data.get("error"):
                    err = data["error"].get("message", "")
                    if "not found" not in err.lower():
                        FAILED_USERS.append(f"{login_name}: {err}")
                
                break

            except Exception as e:
                if attempt == 2:
                    FAILED_USERS.append(f"{login_name}: Exception - {str(e)}")
                await asyncio.sleep(0.5)

async def main():
    limits = httpx.Limits(max_connections=CONCURRENT + 5, max_keepalive_connections=CONCURRENT)
    timeout = httpx.Timeout(30.0, pool=None)

    async with httpx.AsyncClient(verify=False, timeout=timeout, limits=limits) as client:
        client.headers["referer"] = f"https://{SERVER}/ipa"

        print("Авторизация...")
        if not await login(client):
            print("Не удалось авторизоваться!")
            return

        print("Готово. Начинаем удаление.\n")

        semaphore = asyncio.Semaphore(CONCURRENT)
        tasks = [delete_user(client, i, semaphore) for i in range(1, USERS_TO_DELETE + 1)]

        await tqdm_asyncio.gather(*tasks, desc="Удаление пользователей", unit="шт")

        if FAILED_USERS:
            print(f"\nЗавершено с ошибками ({len(FAILED_USERS)}):")
            for err in sorted(FAILED_USERS):
                print(err)
        else:
            print("\nВсе пользователи успешно удалены!")

if __name__ == "__main__":
    asyncio.run(main())