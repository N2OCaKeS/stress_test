import os

class GetEnv:
    '''
    Активация env по:
    - абсолютному пути
    - относительному пути
    - загрузка переменных в словарь
    - работа с перменной по имени(ключу)
    '''
    _activated = False
    _env_vars = {}


    @staticmethod
    def _load_env(env_path: str):
        
        if not os.path.exists(env_path):
            raise FileNotFoundError(f"env файл не найден: {env_path}")
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip()
                os.environ[key] = value
                GetEnv._env_vars[key] = value
        print(f"[GetEnv] Загружено: {env_path}")
        GetEnv._activated = True


    @staticmethod
    def activate_relative(relative_path: str):
        """
        Загрузка env по относительному пути от текущего каталога выполнения.

        Args:
            relative_path (str): относительный путь до файла .env
        """
        if GetEnv._activated:
            return
        env_path = os.path.abspath(os.path.join(os.getcwd(), relative_path))
        GetEnv._load_env(env_path)


    @staticmethod
    def activate_absolute(full_path: str):
        """
        Загрузка env по абсолютному пути.

        Args:
            full_path (str): абсолютный путь до файла .env        
        """
        if GetEnv._activated:
            return
        GetEnv._load_env(full_path)


    @staticmethod
    def get(name: str) -> str:
        """
        Получить определенную переменную

        Args:
            name (str): имя переменной    

        Returns:
            str: значение переменной
        """
        value = os.getenv(name)
        if value is None:
            raise RuntimeError(f"Переменная {name} не найдена в окружении")
        return value


    @staticmethod
    def get_loaded_vars() -> dict:
        """
        Возвращает словарь переменных, загруженных из env.

        Returns:
            dict: словарь всех переменных
        """
        return GetEnv._env_vars.copy()
