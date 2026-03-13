from __future__ import annotations

import os
import subprocess


class SystemCommands:
    @staticmethod
    def check_output_command(command: str):
        result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, universal_newlines=True)
        output, errors = result.communicate()
        output = os.linesep.join([s for s in output.splitlines() if s])
        errors = os.linesep.join([s for s in errors.splitlines() if s])
        return output if not errors else errors

    @staticmethod
    def cmd_with_returncode(command: str):
        return subprocess.run(command, shell=True).returncode

    @staticmethod
    def cmd(command: str):
        return subprocess.run(command, shell=True)

    @staticmethod
    def check_output_command_with_returncode(command: str):
        result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True)
        output, errors = result.communicate()
        output = os.linesep.join([s for s in output.splitlines() if s])
        errors = os.linesep.join([s for s in errors.splitlines() if s])
        return [result.returncode, output if not errors else errors]
