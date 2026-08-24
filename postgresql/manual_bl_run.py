import argparse
from allta import SystemCommands

from new_balance import bl_lib

TEST_TYPE_CHOICES = ["balance", "infosys", "infosys-orel"]
TEST_TYPE_MAP = {
    "balance": "balance",
    "infosys": "info-sys",
    "infosys-orel": "info-sys-orel",
}


class Description(argparse.ArgumentParser):
    def error(self, message):
        print(f"ОШИБКА: {message}\n")
        print("Примеры использования:")
        print("  python manual_bl_run.py --bv 1.7.7.6 --sec o --tt balance")
        print("  python manual_bl_run.py --build-version 1.8.1.UU.2.4 --security-mode s --test-type infosys")
        print("\nДля справки используйте: python manual_bl_run.py --help")
        self.exit(2)

parser = Description(description="Запуск баланса с заданными параметрами")

parser.add_argument('-bv', '--bv', '--build-version', dest='build_version', type=str, required=True,
                    help='Версия билда (например: 1.7.7.6)')

parser.add_argument('-sec', '--sec', '--security-mode', type=str, required=True,
                    choices=['o', 's'],
                    help='Режим защищенности: "o" — Орёл, "s" — Смоленск')

parser.add_argument('-tt', '--tt', '--test-type', type=str, required=True,
                    choices=TEST_TYPE_CHOICES,
                    help='Тип теста: balance, infosys, infosys-orel')

args = parser.parse_args()

# Явная проверка режима безопасности
if args.security_mode not in ['o', 's']:
    print("ОШИБКА: Недопустимый режим защищённости.")
    print('Допустимые значения: "o" — Орёл, "s" — Смоленск')
    print("\nПримеры правильного использования:")
    print("python manual_bl_run.py --bv 1.7.7.6 --sec o --tt balance")
    print("python manual_bl_run.py --build-version 1.8.3.3 --security-mode s --test-type infosys")
    exit(1)

type_test = TEST_TYPE_MAP[args.test_type]


print(
    f"\nЗапуск баланса с версией {args.build_version}, "
    f"режим защищённости: {args.security_mode}, тип теста: {args.test_type}"
)
bl_lib.balance(rc=args.build_version, sec_mode=args.security_mode, type_test=type_test)

print("\n\n_____________________________________\n\n")
SystemCommands.check_output_command('cat ./available_packages.txt')
print("\n\n_____________________________________\n\n")

SystemCommands.check_output_command('cat ./psb_info.txt')
print("\n\n_____________________________________\n\n")
print("Результаты тестирования:")
if type_test == "balance":
    SystemCommands.check_output_command('cat ./results_balance.txt')
else:
    SystemCommands.check_output_command('ls ./mrd_load_level*_results.json 2>/dev/null || true')
