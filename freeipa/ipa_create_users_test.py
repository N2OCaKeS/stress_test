import asyncio
from python_freeipa import ClientMeta
from python_freeipa.exceptions import FreeIPAError
import urllib3
import time

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SERVER = "virtual-station1.stress-testing.local"
USER_START = 1
USER_END = 100

client = ClientMeta(SERVER, verify_ssl=False)
client.login_kerberos()

async def create_user(i):
    login = f"user{i}"
    givenname = f"Test{i}"
    sn = f"User{i}"
    cn = f"{givenname} {sn}"

    loop = asyncio.get_running_loop()
    try:
        start_time = time.time()
        await loop.run_in_executor(
            None,
            lambda: client.user_add(
                login,
                givenname,
                sn,
                cn,
                o_userpassword="Test1234!",
                o_loginshell="/bin/bash"
            )
        )
        end_time = time.time()
        elapsed = end_time - start_time
        print(f"Создан {login} за {elapsed:.3f} секунд")
        return elapsed
    except FreeIPAError as e:
        print(f"ошибка {login}: {e}")

async def main():
    start_total = time.time()
    tasks = [create_user(i) for i in range(USER_START, USER_END)]
    elapsed_times = await asyncio.gather(*tasks)
    end_total = time.time()

    total_time = end_total - start_total
    successful_users = sum(1 for t in elapsed_times if t > 0)
    average_time_per_user = total_time / successful_users if successful_users > 0 else 0

    print(successful_users)
    print(total_time)
    print(average_time_per_user)

if __name__ == "__main__":
    asyncio.run(main())
    print("все пользователи созданы")