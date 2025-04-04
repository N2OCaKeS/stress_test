import subprocess
import sys
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("-t", "--test",
                    type=str,
                    choices=["web"],
                    help="Choose test name.",
                    default=1,
                    dest="USERS")
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
    run_command("source /home/u/python/Python-3.12.1/venv/bin/activate && python3 publish.py")
else: "Тест не найден"