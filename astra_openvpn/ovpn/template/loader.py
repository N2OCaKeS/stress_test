import asyncio
import argparse
import time
from os.path import exists

async def _spawn_detached(cmd: str) -> None:
    await asyncio.create_subprocess_shell(
        cmd,
        stdin=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )

class LoaderClient:
    def __init__(self, client_start: int, client_count: int, client_per_minutes: int):
        if client_per_minutes <= 0:
            raise SystemExit("--client_per_minutes должен быть > 0")
        if client_count <= 0:
            raise SystemExit("--client_count должен быть > 0")
        self.start_idx = int(client_start)
        self.count = int(client_count)
        self.per_min = int(client_per_minutes)
        self.indices = list(range(self.start_idx, self.start_idx + self.count))

    async def _kickoff_one_client(self, idx: int) -> None:
        cfg_dir = f"/home/u/openvpn/clients_keys/tester{idx}"

        if not exists(f"{cfg_dir}/client.ovpn"):
            return
        sh = f"""sudo bash /home/u/loader.sh {idx}"""
        await _spawn_detached(sh)

    async def run(self) -> None:
        period = 60.0 / float(self.per_min)
        next_deadline = time.monotonic()
        for idx in self.indices:
            await self._kickoff_one_client(idx)
            # соблюдаем скорость
            next_deadline += period
            sleep_s = next_deadline - time.monotonic()
            if sleep_s > 0:
                await asyncio.sleep(sleep_s)

def parse_args():
    p = argparse.ArgumentParser(description="Создание OpenVPN-клиентов с заданной скоростью (4 шага, без ожиданий).")
    p.add_argument("--client_per_minutes", type=int, default=30, dest="client_per_minutes")
    p.add_argument("--client_start",        type=int, default=0,  dest="client_start")
    p.add_argument("--client_count",        type=int, default=30, dest="client_count")
    return p.parse_args()

if __name__ == "__main__":
    args = parse_args()
    lc = LoaderClient(args.client_start, args.client_count, args.client_per_minutes)
    asyncio.run(lc.run())