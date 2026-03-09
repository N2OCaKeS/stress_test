import subprocess

subprocess.run("sudo gcc -o /home/u/xfs.mnt/test2 -fno-inline -falign-functions=4096 /home/u/test2.c", shell=True)

with open("/home/u/test2_output.txt", "w") as test2_file:
    test2_file.write("OK")

ret_code = subprocess.run("/home/u/xfs.mnt/test2", shell=True).returncode
with open("/home/u/test2_rt.txt", "a") as test2_rt_file:
    test2_rt_file.write(f"\nКод возврата: {ret_code}")

if ret_code == 139 or ret_code == -11:
    with open("/home/u/test2_output.txt", "w") as test2_file:
        test2_file.write("Ошибка сегментирования")
