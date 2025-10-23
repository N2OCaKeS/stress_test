import time
from functools import wraps


class BaseDecorators:
    """
    Класс с базовыми декораторами.

    Основные функции:
    - trycorator: Декоратор для оборачивания функции в блок try-except.
    """

    def trycorator(function):
        """
        Данный декоратор оборачивает функцию в try except
        """

        def wrapper(*args, **kwargs):
            try:
                result = function(*args, **kwargs)
                return result
            except Exception as e:
                print(
                    f"Function: {function.__name__}\nError is: {str(type(e).__name__)}\nMessage: {str(e)}"
                )
                return result

        return wrapper

    def timer(function):
        "Оборачивает функцию в таймер и выводит время выполнения функции"

        @wraps(function)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            result = function(*args, **kwargs)
            total_seconds = time.perf_counter() - t0
            
            # Разбиваем время на компоненты
            hours = int(total_seconds // 3600)
            minutes = int((total_seconds % 3600) // 60)
            seconds = total_seconds % 60
            milliseconds = (seconds - int(seconds)) * 1000
            seconds_int = int(seconds)
            
            time_parts = []
            time_parts.append(f"{hours} часов")
            time_parts.append(f"{minutes} минут") 
            time_parts.append(f"{seconds_int} секунд")
            time_parts.append(f"{milliseconds:.0f} миллисекунд")
            
            time_str = " ".join(time_parts)
            print(f"[{function.__name__}] Выполнялась: {time_str}")
            
            return result
        
        return wrapper
