import subprocess
from os import linesep



class system:
    """
    Класс для обращения к системе
    """
    @staticmethod
    def check_output_command(command: str) -> str:
        result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        output, errors = result.communicate()
        output = linesep.join([s for s in output.splitlines() if s])
        errors = linesep.join([s for s in errors.splitlines() if s])
        return output if not errors else errors

    @staticmethod
    def cmd_with_returncode(command: str) -> int:
        return subprocess.run(command, shell=True).returncode

    @staticmethod
    def cmd(command: str):
        return subprocess.run(command, shell=True)