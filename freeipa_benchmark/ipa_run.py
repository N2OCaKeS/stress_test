from argparse import ArgumentParser
from libs.libipa import remote_exec, cmd

parser = ArgumentParser()

if __name__ == "__main__":
    """
        Ининциализация КД
    """
    remote_exec("cd ~/git/stress_test/freeipa_benchmark && sudo venv/bin/python ipa_init_dc.py", "server")
    
    """
        Инициализация реплики
    """
    remote_exec("cd ~/git/stress_test/freeipa_benchmark && sudo venv/bin/python ipa_init_replica.py", "replica")

    """
        Ввод клиентов в домен
    """