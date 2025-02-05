from main import run_command

run_command("sudo apt install python3.11-venv -y")
run_command("python3 -m venv venv")
run_command("pip install -r requirements.txt")
print(run_command("pwd"))