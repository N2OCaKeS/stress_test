import logging
import sys

from pathlib import Path
from typing import Optional
from config.conf import MAIN_DIR


class Colors:
    RESET = '\033[0m'
    # Основные цвета
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    # Жирные варианты
    BOLD_RED = '\033[1;31m'
    BOLD_YELLOW = '\033[1;33m'
    BOLD_GREEN = '\033[1;32m'
    BOLD_CYAN = '\033[1;36m'


class ColoredFormatter(logging.Formatter):
    """
    Форматтер с цветовой подсветкой уровня логирования
    """
    LEVEL_COLORS = {
        logging.DEBUG: Colors.CYAN,
        logging.INFO: Colors.GREEN,
        logging.WARNING: Colors.YELLOW,
        logging.ERROR: Colors.RED,
        logging.CRITICAL: Colors.BOLD_RED,
    }
    
    def format(self, record):
        # Сохраняем оригинальный levelname
        original_levelname = record.levelname
        
        # Добавляем цвет для levelname
        color = self.LEVEL_COLORS.get(record.levelno, Colors.RESET)
        record.levelname = f"{color}{original_levelname}{Colors.RESET}"
        
        # Форматируем сообщение
        result = super().format(record)
        
        # Восстанавливаем оригинальный levelname
        record.levelname = original_levelname
        
        return result



class OSBLogger:
    
    """
    Основной логгер для всего приложения с функцией 
    параллельного вывода в терминал. 
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
        log_file: Optional[str] = f"{MAIN_DIR}/logs/osbench.log",
        log_level: str = "DEBUG",
        console: bool = True,
        colored_console: bool = True
    ):
        
        """
        Настройка логгера
        
        Args:
            name: Имя логгера
            log_file: Путь к файлу лога (если не нужен, оставить None)
            log_level: DEBUG, INFO, WARNING, ERROR
            console: Выводить ли в консоль
            colored_console: Добавить цвета
        """

        if self._setup:
            return
        
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, log_level.upper()))
        self.logger.handlers.clear()
        
        # Форматтер для файла (без цветов)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s: - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Форматтер для консоли (с цветами или без)
        if colored_console:
            console_formatter = ColoredFormatter(
                '%(asctime)s - %(levelname)s: - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
        else:
            console_formatter = logging.Formatter(
                '%(asctime)s - %(levelname)s: - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
        
        # Консольный вывод
        if console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(console_formatter)
            self.logger.addHandler(console_handler)
        
        # Файловый вывод
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)
        
        self._setup = True
    
    def _get_logger(self):
        if not self._setup:
            self.setup()
        return self.logger
    
    def enable_console(self, enabled: bool):
        """
        Включить/выключить вывод в консоль
        """
        if not self._setup:
            self.setup()
        
        # Удаляем все консольные обработчики
        self.logger.handlers = [h for h in self.logger.handlers 
                                if not isinstance(h, logging.StreamHandler)]
        
        if enabled:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(ColoredFormatter(
                '%(asctime)s - %(levelname)s: - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            ))
            self.logger.addHandler(console_handler)
    
    @classmethod
    def set_console(cls, enabled: bool):
        cls().enable_console(enabled)
        
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

