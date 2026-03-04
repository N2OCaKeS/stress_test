import subprocess

subprocess.run("gcc -o xfs.mnt/test2 -fno-inline -falign-functions=4096 test2.c")

with open("test2_output.txt", "w") as test2_file:
    test2_file.write("OK")

ret_code = subprocess.run("xfs.mnt/test2",shell=True).returncode
if ret_code == 139:
    with open("test2_output.txt", "w") as test2_file:
        test2_file.write("Ошибка сегментирования")
