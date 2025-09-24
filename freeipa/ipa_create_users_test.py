import asyncio
from python_freeipa import ClientMeta
from python_freeipa.exceptions import FreeIPAError
import urllib3

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
        print(f"создан {login}")
    except FreeIPAError as e:
        print(f"ошибка {login}: {e}")

async def main():
    tasks = [create_user(i) for i in range(USER_START, USER_END)]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
    print("все пользователи созданы")