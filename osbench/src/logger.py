import logging
import sys

from pathlib import Path
from typing import Optional


class OSBLogger:
    
    """
    Простой логгер для всего приложения
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self.logger = None
        self._setup = False
    
    def setup(
        self,
        name: str = "OSBench",
        log_file: Optional[str] = None,
        log_level: str = "INFO",
        console: bool = True
    ):
        
        """
        Настройка логгера
        
        Args:
            name: Имя логгера
            log_file: Путь к файлу лога (если не нужен, оставить None)
            log_level: DEBUG, INFO, WARNING, ERROR
            console: Выводить ли в консоль
        """

        if self._setup:
            return
        
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, log_level.upper()))
        self.logger.handlers.clear()
        
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Консольный вывод
        if console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)
        
        # Файловый вывод
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
        
        self._setup = True
    
    def _get_logger(self):
        if not self._setup:
            self.setup()
        return self.logger
    
    @classmethod
    def debug(cls, msg: str):
        cls()._get_logger().debug(msg)
    
    @classmethod
    def info(cls, msg: str):
        cls()._get_logger().info(msg)
    
    @classmethod
    def warning(cls, msg: str):
        cls()._get_logger().warning(msg)
    
    @classmethod
    def error(cls, msg: str):
        cls()._get_logger().error(msg)
    
    @classmethod
    def critical(cls, msg: str):
        cls()._get_logger().critical(msg)


log = OSBLogger()

