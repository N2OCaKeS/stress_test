import subprocess
import os

class _System_Commands:
    """
    Класс для обращения к системе
    """

    @staticmethod
    def check_output_command(command: str) -> str:
        """
        Выполнение команды с проверкой вывода

        Args:
            command (str): команда которая должна быть выполнена

        Returns:
            str: Если не ошибок вывод от команды, если есть то ошибка
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
        Выполнение команды с возвращением кода завершения

        Args:
            command (str): команда которая должна быть выполнена

        Returns:
            int: код завершения
        """
        return subprocess.run(command, shell=True).returncode

    @staticmethod
    def cmd(command: str):
        """
        Выполнение команды без обработки

        Args:
            command (str): команда которая должна быть выполнена

        Returns:
            _type_: _description_
        """
        return subprocess.run(command, shell=True)
