import pytz
import datetime
import traceback
from functools import wraps

def task_logger(log_file="/fastapi_app_stat/logs/tasks.log", only_task=True):
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            border_line = "=" * 50
            current_time = datetime.datetime.now(tz=pytz.timezone('Europe/Moscow')).strftime('%H:%M:%S %d.%m.%Y')
            
            try:
                result = func(self, *args, **kwargs)
                status = "[SUCCESS]"
                return result
            except Exception as e:
                tb_str = traceback.format_exc()
                status = f"[ERROR]\nTRACEBACK:\n{tb_str}"
                raise
            finally:
                krcvers = kwargs.get('rc_version') or kwargs.get('stat_rc_version')
                cattrvers = getattr(self, 'rc_version', None)
                if krcvers:
                    version = krcvers
                elif cattrvers:
                    version = cattrvers
                else:
                    version = "MAIN"
                
                log_entry = [
                        f"{border_line}\n",
                        f"TASK [{self.stat_title} {version}]\n",
                        f"[ {current_time}]\n",
                        f"STATUS {status}\n",
                        f"{border_line}\n\n\n",
                ]

                if not only_task:
                    log_entry.insert(2, f"CLASS: {self.__class__.__name__}")
                
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write("".join(log_entry))
        
        return wrapper
    return decorator