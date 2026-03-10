import subprocess

subprocess.run("gcc -o /home/u/fill /home/u/fill.c", shell=True)

for i in range(20):
    run_fill = subprocess.run("sudo /home/u/fill", shell=True).returncode

