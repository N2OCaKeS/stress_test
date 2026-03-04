import subprocess

subprocess.run("gcc -o fill fill.c", shell=True)

for i in range(20):
    run_fill = subprocess.run("./fill", shell=True).returncode

