import argparse
from new_balance import bl_lib
from allta import SystemCommands

class Description(argparse.ArgumentParser):
    def error(self, message):
        print(f"ОШИБКА: {message}\n")
        print("Примеры использования:")
        print("  python manual_bl_run.py -bv 1.7.7.6 -sec o")
        print("  python manual_bl_run.py --build-version 1.8.1.UU.2.4 --security-mode s")
        print("\nДля справки используйте: python manual_bl_run.py --help")
        self.exit(2)

parser = Description(description="Запуск баланса с заданными параметрами")

parser.add_argument('-bv', '--build-version', type=str, required=True,
                    help='Версия билда (например: 1.7.7.6)')

parser.add_argument('-sec', '--security-mode', type=str, required=True,
                    choices=['o', 's'],
                    help='Режим защищенности: "o" — Орёл, "s" — Смоленск')

args = parser.parse_args()

# Явная проверка режима безопасности
if args.security_mode not in ['o', 's']:
    print("ОШИБКА: Недопустимый режим защищённости.")
    print('Допустимые значения: "o" — Орёл, "s" — Смоленск')
    print("\nПримеры правильного использования:")
    print("python manual_bl_run.py -bv 1.7.7.6 -sec o")
    print("python manual_bl_run.py --build-version 1.8.3.3 --security-mode s")
    exit(1)


print(f"\nЗапуск баланса с версией {args.build_version}, режим защищённости: {args.security_mode}")
bl_lib.balance(rc=args.build_version, sec_mode=args.security_mode)

print("\n\n_____________________________________\n\n")
SystemCommands.check_output_command('cat ./available_packages.txt')
print("\n\n_____________________________________\n\n")

SystemCommands.check_output_command('cat ./psb_info.txt')
print("\n\n_____________________________________\n\n")
print("Результаты тестирования:")
SystemCommands.check_output_command('cat ./results_balance.txt')