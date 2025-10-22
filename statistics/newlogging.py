import pytz
import datetime
import traceback
from functools import wraps

def task_logger(level=2):
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

                if level == 2:
                    log_entry.insert(2, f"CLASS: {self.__class__.__name__}")
                elif level == 3:
                    log_entry.insert()
                
                log_file = f"{self.username}_{self.stat_title}_level{level}.log"
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write("".join(log_entry))
        
        return wrapper
    return decorator