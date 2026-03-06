import subprocess

subprocess.run("sudo gcc -o /home/u/xfs.mnt/test2 -fno-inline -falign-functions=4096 /home/u/test2.c")

with open("/home/u/test2_output.txt", "w") as test2_file:
    test2_file.write("OK")

ret_code = subprocess.run("/home/u/xfs.mnt/test2",shell=True).returncode
if ret_code == 139:
    with open("/home/u/test2_output.txt", "w") as test2_file:
        test2_file.write("Ошибка сегментирования")
