import argparse
import subprocess
import time

ram_worker = 1

parser = argparse.ArgumentParser()
parser.add_argument("-o", "--docker-image",
                    type=str,
                    help="Docker base image for stress testing",
                    default="ubuntu:latest",
                    dest="CONT_NAME")
parser.add_argument("-c", "--count",
                    type=int,
                    help="Choose count of containers",
                    default=5,
                    dest="COUNT")
parser.add_argument("-t", "--timer",
                    type=int,
                    help="timer for test(default 1 min)",
                    default=1,
                    dest="TIMER")
parser.add_argument("-lcpu", "--load-cpu",
                    type=int,
                    help="CPU load (default 2 workers)",
                    default=0,
                    dest="LOAD_CPU")
parser.add_argument("-lram", "--load-ram",
                    type=str,
                    help="RAM load (default 256 RAM)",
                    default=0,
                    dest="LOAD_RAM")
args = parser.parse_args()


def create_docker_compose():
    compose_content = f"version: '3.1'\n\nservices:\n"
    
    compose_content += f"""
  base:
    build:
      context: .
      args:
        BASE_IMAGE: {args.CONT_NAME}
    image: stress_test_image
"""

    # Создаем нужное количество сервисов из этого образа
    for i in range(1, args.COUNT + 1):
        service_name = f"stress_{i}"
        service_content = f"""
  {service_name}:
    image: stress_test_image
    container_name: {service_name}
    command: ["stress", "--vm", "{ram_worker}", "--vm-bytes", "{args.LOAD_RAM}", "--cpu", "{args.LOAD_CPU}", "--timeout", "{args.TIMER * 60}"]
    stdin_open: true
    tty: true
"""
        compose_content += service_content

    with open('docker-compose.yml', 'w') as file:
        file.write(compose_content)

    print("Файл docker-compose.yml успешно создан.")


def run_command(command):
    try:
        result = subprocess.run(command, shell=True, check=True, text=True, capture_output=True)
        print(result.stdout.strip())
        return True
    except subprocess.CalledProcessError as e:
        print("Команда завершилась с ошибкой:")
        print(e)
        return False


try:
    create_docker_compose()
    run_command("docker-compose up -d")

    time.sleep(args.TIMER * 60)

finally:
    run_command("docker-compose down")
    print("Контейнеры остановлены и удалены.")
# try:
#     create_docker_compose()
    
#     if not run_command("docker-compose up -d"):
#         raise RuntimeError("Не удалось запустить контейнеры")

# finally:
#     if not run_command("docker-compose down"):
#         print("Не удалось корректно остановить и удалить контейнеры")
#     else:
#         print("Контейнеры остановлены и удалены.")