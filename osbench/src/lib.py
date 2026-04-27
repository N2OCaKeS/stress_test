
from abc import ABC, abstractmethod
from typing import Union
from functools import wraps

from src.logger import log



class Test(ABC):
    
    """
    Абстрактный конвейер
    """

@abstractmethod
def start_test(cls) -> Union[str, None]:
    """Запуск серии тестов с использованием бенчмарка 'X'"""
    pass
    
@abstractmethod
def get_results(cls) -> Union[str, None]:
    """Сбор результатов тестирования"""
    pass



def status_checker(method):

    """
    Декоратор, который выводит сообщение с статусом, 
    сигнализирующем об успешности выполнения метода
    """

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        log.info(f'Вызван метод "{method.__name__}"')
        try:
            result, status  = method(self, *args, **kwargs)
            if not result and not status:
                log.info(f'Выполнение "{method.__name__}" отменено, установлен статут False\n')
            elif not status:
                log.error(f'При выполнении "{method.__name__}" произошла ошибка\n')
                log.error(result)
                log.info('End logging\n')
                exit(1)
            else:   
                log.info(result)
                log.info(f'Метод "{method.__name__}" успешно выполнен\n')
            return result
        except Exception as e:
            log.error(f'\nERROR:\nMethod: {method.__name__}\nError is: {str(type(e).__name__)}\nMessage: {str(e)}')
            log.info('End logging\n')
            exit(1)
    return wrapper

