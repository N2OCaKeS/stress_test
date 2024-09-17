import logging

main_logger = logging.getLogger("main_statistics_logger")
main_logger.setLevel(logging.DEBUG)

"""HANDLERS"""
handler_info = logging.FileHandler(f"main_statistics_logger.log", mode="a")
handler_info.setLevel(logging.INFO)

handler_debug = logging.FileHandler(f"debug_statistics_logger.log", mode="a")
handler_debug.setLevel(logging.DEBUG)

"""FORMATTERS"""
formatter = logging.Formatter("%(name)s %(asctime)s %(levelname)s %(message)s")

handler_info.setFormatter(formatter)
handler_debug.setFormatter(formatter)

"""Adding handlers in logger"""
main_logger.addHandler(handler_info)
main_logger.addHandler(handler_debug)
