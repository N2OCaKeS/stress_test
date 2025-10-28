import pytz
import datetime
import traceback
from functools import wraps

def task_logger(level=2):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            border_line = "=" * 50
            current_time = datetime.datetime.now(tz=pytz.timezone('Europe/Moscow')).strftime('%H:%M:%S %d.%m.%Y')
            
            try:
                result = func(*args, **kwargs)
                status = "[SUCCESS]"
                return result
            except Exception as e:
                tb_str = traceback.format_exc()
                status = f"[ERROR]\nTRACEBACK:\n{tb_str}"
                raise
            finally:
                krcvers = kwargs.get('rc_version') or kwargs.get('stat_rc_version')
                if krcvers:
                    version = krcvers
                else:
                    version = "MAIN"

                instance = args[0] if args and hasattr(args[0], '__class__') else None
                lvltwostr = f"TASK [{kwargs.get('stat_title')} {version}]\n"
                log_entry = [
                        f"{border_line}\n",
                        f"[ {current_time}]\n",
                        f"STATUS {status}\n",
                        f"{border_line}\n\n\n",
                ]
                log_file = f"logs/{kwargs.get('username')}_{kwargs.get('stat_title')}_level{level}.log"
                if level == 1:
                    log_entry.insert(1, f"TASK [{kwargs.get('stat_title')}]\n")
                    log_file = f"logs/{kwargs.get('username')}_all_statistics.log"
                elif level == 2:
                    log_entry.insert(1, lvltwostr)
                elif level == 3:
                    log_entry.insert(1, lvltwostr)
                    log_entry.insert(3, f"CLASS: [{instance.__class__.__name__}]\n")
                    log_entry.insert(4, f"TYPE TEST: [{kwargs.get('type_test')}]\n")
                
                
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write("".join(log_entry))
        
        return wrapper
    return decorator