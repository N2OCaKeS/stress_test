import subprocess
import os

class SystemCommands:
    """
    Класс для выполнения системных команд.

    Основные функции:
    - Выполнение команды с проверкой вывода.
    - Выполнение команды с возвратом кода завершения.
    - Выполнение команды без обработки результата.

    Этот класс используется для взаимодействия с операционной системой через shell.
    """

    @staticmethod
    def check_output_command(command: str) -> str:
        """
        Выполняет системную команду и возвращает её вывод.

        Args:
            command (str): Команда, которая должна быть выполнена.

        Returns:
            str: Вывод команды, если ошибок нет. В случае ошибки возвращается текст ошибки.
        """
        result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, universal_newlines=True)
        output, errors = result.communicate()
        output = os.linesep.join([s for s in output.splitlines() if s])
        errors = os.linesep.join([s for s in errors.splitlines() if s])
        return output if not errors else errors

    @staticmethod
    def cmd_with_returncode(command: str) -> int:
        """
        Выполняет системную команду и возвращает её код завершения.

        Args:
            command (str): Команда, которая должна быть выполнена.

        Returns:
            int: Код завершения команды.
        """
        return subprocess.run(command, shell=True).returncode

    @staticmethod
    def cmd(command: str):
        """
        Выполняет системную команду без обработки результата.

        Args:
            command (str): Команда, которая должна быть выполнена.

        Returns:
            subprocess.CompletedProcess: Результат выполнения команды.
        """
        return subprocess.run(command, shell=True)

@staticmethod
def check_output_command_with_returncode(command: str) -> list[int | str, str]:
    """
    Выполняет системную команду и возвращает её код возврата и вывод.

    Args:
        command (str): Команда, которая должна быть выполнена.

    Returns:
        list: Массив из двух элементов:
            - int: Код возврата (0 = успех, не 0 = ошибка)
            - str: Вывод команды (stdout) или ошибки (stderr), если она была.
    """
    result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, universal_newlines=True)
    output, errors = result.communicate()
    
    # Очистка от пустых строк
    output = os.linesep.join([s for s in output.splitlines() if s])
    errors = os.linesep.join([s for s in errors.splitlines() if s])
    
    return [result.returncode, output if not errors else errors]