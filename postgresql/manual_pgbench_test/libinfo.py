import os
import re
import sys
import platform
import subprocess
from os import linesep
from typing import Union, Tuple
from datetime import datetime


class system:
    
    """
    Обращение к системе
    """

    @staticmethod
    def command(command: str, returncode=None) -> Tuple[str, bool]:
        """
        Вывод в терминал/лог после завершения команды
        """
        result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, universal_newlines=True, text=True)
        result.wait()
        output, errors = result.communicate()
        output = linesep.join([s for s in output.splitlines() if s])
        errors = linesep.join([s for s in errors.splitlines() if s])
        if returncode:
               return result.returncode
        else:
            if not errors:
                 return output
            else:
                 return errors
            

    # @staticmethod
    # def leave_command(command: str, returncode=None, console=True, debug=False) -> Tuple[str, bool]:
    #     """
    #     Построчный вывод в терминал/лог
    #     """
    #     if debug:
    #         log.info(f"Выполняется команда: {command}")

    #     output_lines = []
    #     error_lines = []

    #     process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE,
    #                                stderr=subprocess.PIPE, text=True, bufsize=1, universal_newlines=True)
        
    #     if not console:
    #         log.set_console(False)
    #         log.info(f"Выполняется команда: {command}")
    #         keepalive_active = False
    #         if debug:
    #             keepalive_active = True
             
    #             def keepalive():
    #                 spinner = ['◐', '◓', '◑', '◒']
    #                 idx = 0
    #                 start = time()
    #                 total_hours = 0
    #                 total_minutes = 0
    #                 total_seconds = 0
    #                 while keepalive_active and process.poll() is None:
    #                     sleep(1)
    #                     if keepalive_active:
    #                         elapsed = int(time() - start)
    #                         total_hours = elapsed // 3600
    #                         total_minutes = (elapsed % 3600) // 60
    #                         total_seconds = elapsed % 60
    #                         sys.stdout.write(f'\r{spinner[idx]}  {total_hours:02d}:{total_minutes:02d}:{total_seconds:02d}')
    #                         sys.stdout.flush()
    #                         idx = (idx + 1) % len(spinner)
    #                 sys.stdout.write(f'\rВыполнено за {total_hours:02d}:{total_minutes:02d}:{total_seconds:02d} \n')
    #                 sys.stdout.flush()

    #             keepalive_thread = threading.Thread(target=keepalive, daemon=True)
    #             keepalive_thread.start()

    #     for line in process.stdout:
    #         log.info(line.rstrip('\n'))  
    #         output_lines.append(line.rstrip('\n'))
    #     for line in process.stderr:  
    #         log.error(line.rstrip('\n'))
    #         error_lines.append(line.rstrip('\n'))

    #     try:
    #         process.wait()
    #     except KeyboardInterrupt:
    #         process.send_signal(signal.SIGINT)
    #         process.wait()
    #         log.warning(f"Команда прервана пользователем: {command}")
    #         raise
        
    #     if not console:
    #         if debug:
    #             keepalive_active = False
    #             keepalive_thread.join(timeout=1)
    #         log.set_console(True)

    #     if process.returncode == 0:
    #         if debug:
    #             log.info(f"Команда '{command}' завершена с кодом: {process.returncode}\n")
    #     else: log.error(f"Команда '{command}' завершена с кодом: {process.returncode}\n")

    #     output = '\n'.join(output_lines)
    #     errors = '\n'.join(error_lines)

    #     code = process.returncode == 0
    #     if returncode:
    #         if not errors:
    #             return output, code
    #         else:
    #             return errors, code
    #     else:
    #         if not errors:
    #             return output, True
    #         else:
    #             return errors, False
            

    @staticmethod
    def get_system_info():
        import psutil
        def _get_kernel():
            try:
                code = system.command("dpkg -s linux-image-`uname -r` | grep Version: | awk '{print $2}'", returncode=True)
                if code == 0:
                    kernel = system.command("dpkg -s linux-image-`uname -r` | grep Version: | awk '{print $2}'")
                    if kernel:
                        return kernel.strip()
            except:
                return None

        def _get_os_name():
            try:
                with open("/etc/astra/build_version", "r") as f:  
                    os_name = "Astra Linux" 
                    os_version = f"{f.read().strip()}"
                return os_name, os_version
            except:
                return None, None

        def _get_linux_cpu_model():
            try:
                with open('/proc/cpuinfo', 'r') as f:
                    for line in f:
                        if 'model name' in line:
                            cpu_model = line.split(':')[1].strip()
                            break
            except:
                cpu_model = "Unknown"
            return cpu_model
        
        cpu_model = platform.processor()
        if cpu_model == "Unknown" or not cpu_model:
            cpu_model = _get_linux_cpu_model()

        os_name, os_version = _get_os_name()
        if not os_name or not os_version:
            os_name = platform.system()
            os_version = platform.release()

        os_kernel = _get_kernel()
        if not os_kernel:
            os_kernel = platform.version()
        
        return {
            'os_name': os_name,
            'os_version': os_version,
            'kernel_version': os_kernel,
            'cpu_model': cpu_model,
            'cpu_cores': psutil.cpu_count(logical=False),
            'cpu_threads': psutil.cpu_count(logical=True),
            'ram_total': f"{psutil.virtual_memory().total / (1024**3):.1f} GB",
            'hostname': platform.node()
        }

    @staticmethod
    def print_info_frame(title: str, rows: list, width: int = 90):
        """
        Вывод информации в рамке из псевдографики, например:

        ┌──────────────────────────────────────────┐
        │  ЗАГОЛОВОК
        ├──────────────────────────────────────────┤
        │  Метка:             значение
        └──────────────────────────────────────────┘
        """
        print("┌" + "─" * width + "┐")
        print(f"│  {title}")
        print("├" + "─" * width + "┤")
        for label, value in rows:
            print(f"│  {label:<20}{value}")
        print("└" + "─" * width + "┘")


class PostgresqlInformation:
    """
    Информация о PostgreSQL
    """

    # Пути соответствуют кластеру setest, который создают
    # psb_db_prep_*.sh для каждой ОС. {pg_version}/{cluster}
    # подставляются автоматически в get_postgresql_config_path()
    postgres_config_path_astra_linux_smol = "/etc/postgresql/{pg_version}/{cluster}/postgresql.conf"
    postgres_config_path_astra_linux_orel = "/etc/postgresql/{pg_version}/{cluster}/postgresql.conf"
    postgres_config_path_debian = "/etc/postgresql/{pg_version}/{cluster}/postgresql.conf"
    postgres_config_path_rhel10_and_redos8 = "/var/lib/pgsql/{pg_version}/setest/postgresql.conf"
    postgres_config_path_alt_linux_sp_10 = "/var/lib/pgsql/setest/postgresql.conf"

    # Параметры конфигурации БД, которые нужно забирать из postgresql.conf
    CONFIG_KEYS = [
        "max_connections",
        "shared_buffers",
        "effective_cache_size",
        "maintenance_work_mem",
        "work_mem",
        "wal_buffers",
        "checkpoint_completion_target",
        "default_statistics_target",
        "random_page_cost",
        "effective_io_concurrency",
        "min_wal_size",
        "max_wal_size",
        "max_worker_processes",
        "max_parallel_workers_per_gather",
        "max_parallel_workers",
        "max_parallel_maintenance_workers",
    ]

    @staticmethod
    def get_postgresql_version():
        try:
            version = system.command("psql --version")
            if version:
                return version.strip()
        except:
            return None

    @staticmethod
    def get_postgresql_major_version():
        """
        Мажорная версия PostgreSQL (например "16") на основе psql --version
        """
        version = PostgresqlInformation.get_postgresql_version()
        if not version:
            return None
        match = re.search(r"(\d+)(?:\.\d+)*", version)
        return match.group(1) if match else None

    @staticmethod
    def _detect_os():
        """
        Определяет ОС/дистрибутив, чтобы подобрать нужный шаблон пути
        до postgresql.conf, соответствующий psb_db_prep_*.sh
        """
        try:
            with open("/etc/astra_version", "r") as f:
                astra_version = f.read()
            return "astra_orel" if "1.8" in astra_version else "astra_linux_smol"
        except (FileNotFoundError, PermissionError):
            pass

        try:
            with open("/etc/os-release", "r") as f:
                os_release = f.read().lower()
        except (FileNotFoundError, PermissionError):
            return None

        if "altlinux" in os_release:
            return "alt_linux_sp_10"
        if "debian" in os_release:
            return "debian"
        if "rhel" in os_release or "redos" in os_release:
            return "rhel10_and_redos8"
        return None

    @staticmethod
    def get_postgresql_config_path():
        """
        Путь до postgresql.conf кластера setest для текущей ОС
        """
        os_type = PostgresqlInformation._detect_os()
        pg_version = PostgresqlInformation.get_postgresql_major_version()

        if os_type in ("astra_linux_smol", "astra_orel", "debian"):
            template = {
                "astra_linux_smol": PostgresqlInformation.postgres_config_path_astra_linux_smol,
                "astra_orel": PostgresqlInformation.postgres_config_path_astra_linux_orel,
                "debian": PostgresqlInformation.postgres_config_path_debian,
            }[os_type]

            base_dir = f"/etc/postgresql/{pg_version}"
            try:
                clusters = os.listdir(base_dir)
            except (FileNotFoundError, TypeError):
                clusters = []
            cluster = next((c for c in clusters if "setest" in c), "setest")
            return template.format(pg_version=pg_version, cluster=cluster)

        if os_type == "rhel10_and_redos8":
            return PostgresqlInformation.postgres_config_path_rhel10_and_redos8.format(pg_version=pg_version)

        if os_type == "alt_linux_sp_10":
            return PostgresqlInformation.postgres_config_path_alt_linux_sp_10

        return None

    @staticmethod
    def get_postgresql_config(config_path: str = None) -> dict:
        """
        Забирает значения CONFIG_KEYS из postgresql.conf и возвращает dict.
        Значения, не найденные или закомментированные в файле, будут равны None.
        """
        config_path = config_path or PostgresqlInformation.get_postgresql_config_path()
        result = {key: None for key in PostgresqlInformation.CONFIG_KEYS}

        if not config_path:
            return result

        try:
            with open(config_path, "r") as f:
                lines = f.readlines()
        except (FileNotFoundError, PermissionError, TypeError):
            return result

        # Незакомментированная строка вида "key = value # comment"
        pattern = re.compile(r"^\s*([a-z_]+)\s*=\s*([^#]+?)\s*(?:#.*)?$")
        for line in lines:
            match = pattern.match(line)
            if not match:
                continue
            key, value = match.group(1), match.group(2).strip().strip("'\"")
            if key in result:
                result[key] = value

        return result

if __name__ == "__main__":
    sys_info = system.get_system_info()
    system.print_info_frame("ИНФОРМАЦИЯ О СИСТЕМЕ", [
        ("ОС:", f"{sys_info['os_name']} {sys_info['os_version']}"),
        ("Ядро:", sys_info['kernel_version']),
        ("Процессор:", sys_info['cpu_model']),
        ("Ядер/потоков:", f"{sys_info['cpu_cores']}/{sys_info['cpu_threads']}"),
        ("ОЗУ:", sys_info['ram_total']),
        ("Хост:", sys_info['hostname']),
    ])
    print()

    pg_rows = [("Версия:", PostgresqlInformation.get_postgresql_version() or "—")]
    pg_config = PostgresqlInformation.get_postgresql_config()
    pg_rows += [(f"{key}:", value or "—") for key, value in pg_config.items()]
    system.print_info_frame("ИНФОРМАЦИЯ О POSTGRESQL", pg_rows)
    print()