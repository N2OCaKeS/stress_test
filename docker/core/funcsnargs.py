import subprocess
import argparse
import core.variables as var


ram_worker = 1

parser = argparse.ArgumentParser()
parser.add_argument("--test",
                    required=True,
                    type=str,
                    choices=["load", "http", "remove", "load-http"],
                    help="Type your test",
                    dest="TEST_TYPE")
parser.add_argument("-o", "--docker-image",
                    type=str,
                    help="Docker base image for stress testing",
                    default="ubuntu:latest",
                    dest="CONT_NAME")
parser.add_argument("-c", "--count",
                    type=int,
                    help="Choose count of containers",
                    default=2,
                    dest="COUNT")
parser.add_argument("-t", "--timer",
                    type=int,
                    help="timer for test(default 1 min)",
                    default=1,
                    dest="TIMER")
parser.add_argument("-lcpu", "--load-cpu",
                    type=int,
                    help="CPU load (default 1 worker)",
                    default=1,
                    dest="LOAD_CPU")
parser.add_argument("-lram", "--load-ram",
                    type=str,
                    help="RAM load (default 256 RAM)",
                    default=256,
                    dest="LOAD_RAM")
args = parser.parse_args()


def create_load_docker_compose(content): 
    run_command("docker build -t stress_test_image ./dockerfiles/load/")
    # Создаем нужное количество сервисов из нагрузочного образа образа
    for i in range(1, args.COUNT + 1):
        service_name = f"stress_{i}"
        service_content = f"""
  {service_name}:
    image: stress_test_image
    container_name: {service_name}
    command: ["stress", "--vm", "1", "--vm-bytes", "{args.LOAD_RAM}", "--cpu", "{args.LOAD_CPU}", "--timeout", "{args.TIMER * 60}"]
    stdin_open: true
    tty: true
"""
        content += service_content

    with open("docker-compose.yml", 'w') as file:
        file.write(content)

    print("Файл docker-compose.yml успешно создан.")
    return content


def create_http_docker_compose(content):
    run_command("docker build -t nginx ./dockerfiles/http/nginx-server/")
    # run_command("docker build --no-cache -t ab ./dockerfiles/http/apache-bench/")
    content += var.http_content

    with open("docker-compose.yml", 'w') as file:
        file.write(content)
    
    print("Файл docker-compose.yml успешно создан.")
    return content
    

def run_command(command):
    try:
        result = subprocess.run(command, shell=True, check=True, text=True, capture_output=True)
        print(result.stdout.strip())
        return True
    except subprocess.CalledProcessError as e:
        print("Команда завершилась с ошибкой:")
        print(e)
        return False