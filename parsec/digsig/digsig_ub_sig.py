import subprocess


ub_path = '/home/u/git/stress_test/linux_system/byte-unixbench-master/UnixBench'
password = '12345678'
key_email = 'load@tester.rbt'
key_path = '/root/.gnupg/openpgp-revocs.d/'


def get_key_id(email):
    result = subprocess.run(f'grep -ro {email} {key_path}', capture_output=True, text=True)
    if result.returncode == 0:
        output = result.stdout.split('/')
        if len(output) > 7:
            return output[6]
    return None


def sign_files():
    key_id = get_key_id(key_email)
    if not key_id:
        print("Ключ не найден!")
        return

    find_command = f'find {ub_path} -type f -executable'
    result = subprocess.run(find_command, capture_output=True, text=True)
    files = result.stdout.splitlines()

    def __sign(key, file):
        bsign_command = f'bsign-integrator -s -k={key} {file}'
        print(f"Подписываем файл: {file}")
        process = subprocess.Popen(bsign_command, stdin=subprocess.PIPE, text=True)
        process.communicate(input=f"{password}\n")
    [__sign(key_id, file) for file in files]
        


sign_files()