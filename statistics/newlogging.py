from functools import wraps
import traceback
import datetime
from functools import wraps

def task_logger(log_file="/fastapi_app_stat/logs/tasks.log"):
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            border_line = "=" * 50
            current_time = datetime.datetime.now().strftime('%H:%M:%S %d-%m-%Y')
            
            try:
                result = func(self, *args, **kwargs)
                status = "SUCCESS"
                return result
            except Exception as e:
                tb_str = traceback.format_exc()
                status = f"ERROR\nTRACEBACK:\n{tb_str}"
            finally:
                version = "MAIN" if not kwargs['rc_version'] else kwargs['rc_version']
                log_entry = (
                    f"{border_line}\n"
                    f"TASK [{self.stat_title} {version}]\n"
                    f"[ {current_time}]\n"
                    f"STATUS [{status}]\n"
                    f"{border_line}\n\n\n"
                )
                
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(log_entry)
        
        return wrapper
    return decorator