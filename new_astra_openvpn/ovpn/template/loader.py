#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
loader_client.py — равномерный генератор OpenVPN-клиентов в отдельных netns
с преднастройкой под Astra Linux (патчи конфигов под 1.7/1.8).

CLI (ровно три параметра):
  --client_per_minutes  (сколько клиентов создавать в минуту, стабильно)
  --client_start        (с какого testerN начинать)
  --client_count        (сколько клиентов всего создать)

Поведение:
- Каждые (60 / --client_per_minutes) секунд создаётся РОВНО один клиент,
  пока не будет создано --client_count клиентов.
- Для каждого клиента создаётся (или пересоздаётся) netns: ./vpn.sh stop <ns>; ./vpn.sh start <ns> <A.B.C> --no-tmux
  (vpn.sh настраивает veth, маршрут, NAT и DNS внутри ns).
- OpenVPN запускается внутри netns, БЕЗ переподключений при ошибках/обрыве.
- iperf запускается в том же netns, отложенно (после появления tunN), нагрузка идёт через VPN.
- Без файловых логов; вывод процессов уводится в /dev/null; процессы продолжают жить после выхода скрипта.
- Перед запуском нагрузчика выполняются Astra-специфичные правки конфигов (1.7/1.8),
  которые вы просили: правка client.ovpn у всех tester{0..9999}, правка server.conf и рестарт служб.

Требования:
- root (ip netns), /home/u/vpn.sh
- конфиги клиентов: /home/u/openvpn/clients_keys/tester{N}/client.ovpn
"""

import asyncio
import argparse
import time
import os
from os.path import exists
from allta import SystemCommands

# ---------------- системные обёртки ----------------


# ---------------- Astra-подготовка ----------------

def astra_prepare_if_needed():
    """
    Выполняет запрошенные действия:
      - Если /etc/astra_version == "1.8" и host != testvm1:
          * для tester0..tester9999: заменить grasshopper-cbc -> kuznyechik-cbc
            и дописать data-ciphers/auth в client.ovpn
        Дополнительно на сервере (если есть /etc/openvpn/server.conf):
          * дописать те же параметры, перезапустить astra-openvpn-server и iperf-server
      - Если /etc/astra_version == "1.7" и host != testvm1:
          * для tester0..tester9999: дописать ncp-disable в client.ovpn
        На сервере: дописать ncp-disable, стартануть astra-openvpn-server и iperf-server
    """
    sys_cls = SystemCommands
    av = sys_cls.check_output_command("cat /etc/astra_version")
    host = sys_cls.check_output_command("hostname -s")

    # ничего не делаем, если файла версии нет
    if not av:
        return

    # Вариант 1.8
    if av == "1.8":
        if host != "testvm1":
            # Правка всех client.ovpn tester{0..9999}
            for i in range(0, 10000):
                base = f"/home/u/openvpn/clients_keys/tester{i}"
                if not exists(f"{base}/client.ovpn"):
                    continue
                # sed на замену grasshopper-cbc -> kuznyechik-cbc
                sys_cls.cmd(f"sed -i 's/grasshopper-cbc/kuznyechik-cbc/g' {base}/client.ovpn")
                # добавить шифры и auth
                sys_cls.cmd(
                    "bash -lc "
                    f"\"printf '\\n%s\\n' 'data-ciphers kuznyechik-cbc' 'auth id-tc26-gost3411-12-512' "
                    f">> {base}/client.ovpn\""
                )

        if exists("/etc/openvpn/server.conf"):
            # добавить параметры на сервере
            SystemCommands.cmd(
                "bash -lc "
                "\"printf '\\n%s\\n' 'data-ciphers kuznyechik-cbc' 'auth id-tc26-gost3411-12-512' "
                ">> /etc/openvpn/server.conf\""
            )
            # рестарты служб
            SystemCommands.cmd("astra-openvpn-server start")
            SystemCommands.cmd("systemctl daemon-reload && systemctl restart iperf-server.service")
            # можно оставить вывод netstat в консоль
            out = SystemCommands.check_output_command("netstat -tulpn | grep 5001")
            if out:
                print(out)
        else:
            print("Конфигурация сервера не найдена в /etc/openvpn/server.conf")

    # Вариант 1.7
    elif av == "1.7":
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

# ---------------- async helpers ----------------

async def _run(cmd: str) -> None:
    """Запуск shell-команды (async). Бросает исключение при ненулевом RC."""
    proc = await asyncio.create_subprocess_shell(cmd)
    rc = await proc.wait()
    if rc != 0:
        raise RuntimeError(f"CMD failed ({rc}): {cmd}")

# ---------------- core ----------------

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
        """
        Пересоздаёт/поднимает ns и настраивает сеть через /home/u/vpn.sh.
        ВАЖНО: второй аргумент vpn.sh — БАЗА A.B.C (скрипт сам делает .1/.2).
        """
        ns = f"vpn{n}"
        # детерминированная база: 172.<X>.<Y>
        third_octet = 31 + ((n // 200) % 100)
        last_octet  = 10 + (n % 200)
        addrbase = f"172.{third_octet}.{last_octet}"

        # чистим/пересоздаём ns
        await _run(f"cd /home/u && ./vpn.sh stop {ns} >/dev/null 2>&1 || true")
        await _run(f"cd /home/u && ./vpn.sh start {ns} {addrbase} --no-tmux")
        return ns

    async def _start_openvpn(self, idx: int, ns: str) -> bool:
        """
        Запускает openvpn в ns без переподключений.
        Конфиг клиента: /home/u/openvpn/clients_keys/tester{idx}/client.ovpn
        """
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
        """
        В том же ns ждём появления tun{idx} (до 120с), затем стартуем iperf (UDP 16M) на 60 минут.
        """
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
        # 1) ns с сетью (vpn.sh)
        ns = await self._ensure_netns(idx)
        # 2) openvpn без реконнектов
        ok = await self._start_openvpn(idx, ns)
        if ok:
            # 3) iperf — отложенно, когда поднимется tun
            await self._start_iperf_deferred(ns, idx)

    async def run(self) -> None:
        period = 60.0 / float(self.per_min)  # секунд между клиентами
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

# ---------------- argparse / main ----------------

def parse_args():
    p = argparse.ArgumentParser(description="Равномерное создание OpenVPN-клиентов по tester{N} в отдельных netns.")
    p.add_argument("--client_per_minutes", type=int, default=30, required=True, dest="client_per_minutes")
    p.add_argument("--client_start",        type=int, default=30, required=True, dest="client_start")
    p.add_argument("--client_count",        type=int, default=30, required=True, dest="client_count")
    return p.parse_args()

if __name__ == "__main__":
    # 0) Astra-подготовка (как просили)
    try:
        astra_prepare_if_needed()
    except Exception as e:
        # не прерываем нагрузчик, если подготовка не удалась
        print(f"[astra-prepare] предупреждение: {e}")

    # 1) Запуск нагрузчика
    args = parse_args()
    lc = LoaderClient(args.client_start, args.client_count, args.client_per_minutes)
    asyncio.run(lc.run())
