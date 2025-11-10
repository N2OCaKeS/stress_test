import asyncio
import argparse
import time
import os
from os.path import exists
from allta import SystemCommands


def astra_prepare_if_needed():
    sys_cls = SystemCommands
    av = sys_cls.check_output_command("cat /etc/astra_version")
    host = sys_cls.check_output_command("hostname -s")

    if not av:
        return

    # Вариант 1.8
    if av.startswith("1.8"):
        if host != "testvm1":
            for i in range(0, 10000):
                base = f"/home/u/openvpn/clients_keys/tester{i}"
                if not exists(f"{base}/client.ovpn"):
                    continue
                sys_cls.cmd(f"sed -i 's/grasshopper-cbc/kuznyechik-cbc/g' {base}/client.ovpn")
                sys_cls.cmd(
                    "bash -lc "
                    f"\"printf '\\n%s\\n' 'data-ciphers kuznyechik-cbc' 'auth id-tc26-gost3411-12-512' "
                    f">> {base}/client.ovpn\""
                )

        if exists("/etc/openvpn/server.conf"):
            SystemCommands.cmd(
                "bash -lc "
                "\"printf '\\n%s\\n' 'data-ciphers kuznyechik-cbc' 'auth id-tc26-gost3411-12-512' "
                ">> /etc/openvpn/server.conf\""
            )
            SystemCommands.cmd("astra-openvpn-server start")
            SystemCommands.cmd("systemctl daemon-reload && systemctl restart iperf-server.service")
            out = SystemCommands.check_output_command("netstat -tulpn | grep 5001")
            if out:
                print(out)
        else:
            print("Конфигурация сервера не найдена в /etc/openvpn/server.conf")

    # Вариант 1.7
    elif av.startswith("1.7"):
        if host != "testvm1":
            for i in range(0, 10000):
                base = f"/home/u/openvpn/clients_keys/tester{i}"
                if not exists(f"{base}/client.ovpn"):
                    continue
                SystemCommands.cmd(
                    f"bash -lc \"printf '\\n%s\\n' 'ncp-disable' >> {base}/client.ovpn\""
                )

        if exists("/etc/openvpn/server.conf"):
            SystemCommands.cmd(
                "bash -lc \"printf '\\n%s\\n' 'ncp-disable' >> /etc/openvpn/server.conf\""
            )
            SystemCommands.cmd("astra-openvpn-server start")
            SystemCommands.cmd("systemctl daemon-reload && systemctl start iperf-server")
            out = SystemCommands.check_output_command("netstat -tulpn | grep 5001")
            if out:
                print(out)
        else:
            print("Конфигурация сервера не найдена в /etc/openvpn/server.conf")


async def _run(cmd: str) -> None:
    proc = await asyncio.create_subprocess_shell(cmd)
    rc = await proc.wait()
    if rc != 0:
        raise RuntimeError(f"CMD failed ({rc}): {cmd}")


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

    async def _ensure_netns(self, n: int) -> str:
        ns = f"vpn{n}"
        third_octet = 31 + ((n // 200) % 100)
        last_octet  = 10 + (n % 200)
        addrbase = f"172.{third_octet}.{last_octet}"

        # чистим/пересоздаём ns
        await _run(f"cd /home/u && ./vpn.sh stop {ns} >/dev/null 2>&1 || true")
        await _run(f"cd /home/u && ./vpn.sh start {ns} {addrbase} --no-tmux")
        return ns

    async def _start_openvpn(self, idx: int, ns: str) -> bool:
        tun = f"tun{idx}"
        cfg_dir = f"/home/u/openvpn/clients_keys/tester{idx}"
        if not exists(f"{cfg_dir}/client.ovpn"):
            return False

        cmd = (
            f'ip netns exec {ns} bash -lc '
            f'"cd {cfg_dir} && nohup openvpn --config client.ovpn '
            f'--dev {tun} --auth-nocache '
            f'--resolv-retry 0 --connect-retry-max 1 --connect-timeout 10 '
            f'--ping 10 --ping-exit 60 --remap-usr1 SIGTERM '
            f'>/dev/null 2>&1 & disown"'
        )
        await _run(cmd)
        return True

    async def _start_iperf_deferred(self, ns: str, idx: int) -> None:
        tun = f"tun{idx}"
        cmd = (
            f'ip netns exec {ns} bash -lc '
            f'"end=$((SECONDS+120)); '
            f'while (( SECONDS < end )); do ip link show dev {tun} >/dev/null 2>&1 && break; sleep 0.5; done; '
            f'ip link show dev {tun} >/dev/null 2>&1 || exit 0; '
            f'nohup iperf -c 10.8.0.1 -u -b 16M -t $((60*60)) -i 5 >/dev/null 2>&1 & disown"'
        )
        await _run(cmd)

    async def _start_one_client(self, idx: int) -> None:
        ns = await self._ensure_netns(idx)
        ok = await self._start_openvpn(idx, ns)
        if ok:
            await self._start_iperf_deferred(ns, idx)

    async def run(self) -> None:
        period = 60.0 / float(self.per_min)
        next_deadline = time.monotonic()
        tasks = []
        for idx in self.indices:
            tasks.append(asyncio.create_task(self._start_one_client(idx)))
            next_deadline += period
            sleep_s = next_deadline - time.monotonic()
            if sleep_s > 0:
                await asyncio.sleep(sleep_s)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def parse_args():
    p = argparse.ArgumentParser(description="Равномерное создание OpenVPN-клиентов по tester{N} в отдельных netns.")
    p.add_argument("--client_per_minutes", type=int, default=30, required=True, dest="client_per_minutes")
    p.add_argument("--client_start",        type=int, default=30, required=True, dest="client_start")
    p.add_argument("--client_count",        type=int, default=30, required=True, dest="client_count")
    return p.parse_args()

if __name__ == "__main__":
    try:
        astra_prepare_if_needed()
    except Exception as e:
        print(f"[astra-prepare] предупреждение: {e}")

    # 1) Запуск нагрузчика
    args = parse_args()
    lc = LoaderClient(args.client_start, args.client_count, args.client_per_minutes)
    asyncio.run(lc.run())
