import subprocess
import sys
import argparse
from libs.docker_conf import VENV_PATH


parser = argparse.ArgumentParser()
parser.add_argument("-t", "--test",
                    type=str,
                    choices=["web"],
                    help="Choose test name.",
                    dest="TEST")
args = parser.parse_args()


def run_command(command):
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=True,
            text=True,
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        return True
    except subprocess.CalledProcessError as e:
        print("Команда завершилась с ошибкой:")
        print(e)
        return False


if args.TEST == "web":
    run_command("cd ./site && bash prepare.sh")
    run_command("cd ./site && bash start.sh final")
    run_command(f"source {VENV_PATH}/activate && python3 publish.py")
else: "Тест не найден"
