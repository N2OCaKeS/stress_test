import asyncio
import httpx
import time

SERVER = "lowserver.stress-testing.local"
ADMIN_PASS = "12345678"

USER_CREATE_START = 200
USER_CREATE_MAX = 1000
USER_CREATE_STEP = 200

async def create_users(client, count):
    print(f"Создаём {count} пользователей (user1 — user{count})")
    start_time = time.perf_counter()

    async def add(user_id):
        login = f"user{user_id}"
        await client.post(
            f"https://{SERVER}/ipa/session/json",
            json={
                "method": "user_add",
                "params": [[login], {
                    "givenname": "Test",
                    "sn": "Тестов",
                    "userpassword": "123456",
                    "random": False
                }],
                "id": user_id
            }
        )

    await asyncio.gather(*(add(i) for i in range(1, count + 1)))

    create_time = time.perf_counter() - start_time
    avg_time_per_user = create_time / count if count > 0 else 0
    users_per_second = count / create_time if create_time > 0 else 0
    
    return create_time, count, avg_time_per_user, users_per_second

async def delete_users(client, count):
    print(f"Удаляем {count} пользователей (user1 — user{count})")
    start_time = time.perf_counter()

    async def delete(user_id):
        login = f"user{user_id}"
        await client.post(
            f"https://{SERVER}/ipa/session/json",
            json={
                "method": "user_del",
                "params": [[login], {}],
                "id": user_id
            }
        )

    await asyncio.gather(*(delete(i) for i in range(1, count + 1)))

    delete_time = time.perf_counter() - start_time

    return delete_time

async def main():
    total_start = time.perf_counter()

    limits = httpx.Limits(max_connections=USER_CREATE_MAX, max_keepalive_connections=USER_CREATE_MAX // 2)
    timeout = httpx.Timeout(120.0, pool=None)
    async with httpx.AsyncClient(verify=False, timeout=timeout, limits=limits) as client:
        client.headers["referer"] = f"https://{SERVER}/ipa"

        await client.post(
            f"https://{SERVER}/ipa/session/login_password",
            data={"user": "admin", "password": ADMIN_PASS}
        )
        print("Авторизация успешна!\n")

        results = []

        for user_count in range(USER_CREATE_START, 
                                USER_CREATE_MAX + USER_CREATE_STEP, 
                                USER_CREATE_STEP):

            create_time, created_count, avg_time_per_user, users_per_second = await create_users(client, user_count)
            await asyncio.sleep(120)
            delete_time = await delete_users(client, created_count)

            results.append({
                "users": user_count,
                "create_time": create_time,
                "delete_time": delete_time,
                "avg_time_per_user": avg_time_per_user,
                "users_per_second": users_per_second,
            })

            with open("ipa_report.txt", "a+") as report_file:
                report_file.write(f"{user_count} {create_time} {avg_time_per_user} {users_per_second}\n")


    total_time = time.perf_counter() - total_start
    
    print(f"Общее время выполнения испытания: {total_time} секунд")

if __name__ == "__main__":
    asyncio.run(main())
