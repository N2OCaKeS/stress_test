import os
from time import sleep

from fastapi import FastAPI, Depends, HTTPException, Response, Query
from fastapi.responses import FileResponse
from starlette.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Set
from atlassian.errors import ApiPermissionError

from statistics_st import BaseStatistics, FreeIpaStatistics, VirtStatistics, ParsecStatistics, PostgreSQLStatistics, DockerStatstics, FileSystemStatistics
from parsers import BaseParser, ApacheParser, ParsecParser

from utils import UtilForReadLogs, UtilGetTraceback
from logging_conf import main_logger

app = FastAPI(
    title="Statistics"
)

class Statistics(BaseModel):
    title_statistics: str
    username: str
    token: str
    set_of_test_types: Set
    comparison_list:  Optional[List] = None
    comparison_kernel_list:  Optional[List] = None

class Auth(BaseModel):
    username: str
    token: str


@app.post("/base-statistics")
def base_statistics_api(body: Statistics):
    if body.title_statistics == "Apache":
        parser = ApacheParser
    elif body.title_statistics == "Parsec":
        parser = ParsecParser
    else:
        parser = BaseParser
    base_stat = BaseStatistics(stat_title=body.title_statistics,
                               username=body.username,
                               tokenconf=body.token,
                               set_of_test_types=body.set_of_test_types,
                               comparison_list=body.comparison_list,
                               comparison_kernel_list=body.comparison_kernel_list,
                               score_parser=parser)
    response = {
        "status": "",
        "message": ""
    }
    try:
        base_stat.create()
        response['status'] = "success"
        response["message"] = "Все прошло успешно"
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        base_stat.create()
        response["status"] = "warning"
        response["message"] = "Все должно было отработать но была ошибка ApiPermissionError, после нее создание статистики было вызвано повторно"
    except Exception as error:
        message_error = UtilGetTraceback.get_traceback(e=error)
        response["status"] = "error"
        response["message"] = message_error

    return response

@app.post("/freeipa-statistics")
def freeipa_statistics_api(body: Statistics):
    freeipa_stat = FreeIpaStatistics(stat_title=body.title_statistics,
                                     username=body.username,
                                     tokenconf=body.token,
                                     set_of_test_types=body.set_of_test_types,
                                     comparison_list=body.comparison_list,
                                     comparison_kernel_list=body.comparison_kernel_list,
                                     )
    response = {
        "status": "",
        "message": ""
    }
    try:
        freeipa_stat.create()
        response['status'] = "success"
        response["message"] = "Все прошло успешно"
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        freeipa_stat.create()
        response["status"] = "warning"
        response["message"] = "Все должно было отработать но была ошибка ApiPermissionError, после нее создание статистики было вызвано повторно"
    except Exception as error:
        message_error = UtilGetTraceback.get_traceback(e=error)
        response["status"] = "error"
        response["message"] = message_error
    return response

@app.post("/virt-statistics")
def virt_statistics_api(body: Statistics):
    virt_stat = VirtStatistics(stat_title=body.title_statistics,
                               username=body.username,
                               tokenconf=body.token,
                               set_of_test_types=body.set_of_test_types,
                               comparison_list=body.comparison_list,
                               comparison_kernel_list=body.comparison_kernel_list,
                               )
    response = {
        "status": "",
        "message": ""
    }
    try:
        virt_stat.create()
        response['status'] = "success"
        response["message"] = "Все прошло успешно"
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        virt_stat.create()
        response["status"] = "warning"
        response["message"] = "Все должно было отработать но была ошибка ApiPermissionError, после нее создание статистики было вызвано повторно"
    except Exception as error:
        message_error = UtilGetTraceback.get_traceback(e=error)
        response["status"] = "error"
        response["message"] = message_error
    return response

@app.post("/parsec-statistics")
def parsec_statistics_api(body: Statistics):
    parsec_stat = ParsecStatistics(stat_title=body.title_statistics,
                                   username=body.username,
                                   tokenconf=body.token,
                                   set_of_test_types=body.set_of_test_types,
                                   comparison_list=body.comparison_list,
                                   comparison_kernel_list=body.comparison_kernel_list,)
    response = {
        "status": "",
        "message": ""
    }
    try:
        parsec_stat.create()
        response['status'] = "success"
        response["message"] = "Все прошло успешно"
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        parsec_stat.create()
        response["status"] = "warning"
        response["message"] = "Все должно было отработать но была ошибка ApiPermissionError, после нее создание статистики было вызвано повторно"
    except Exception as error:
        message_error = UtilGetTraceback.get_traceback(e=error)
        response["status"] = "error"
        response["message"] = message_error
    return response


@app.post("/postgresql-statistics")
def postgresql_statistics_api(body: Statistics):
    postgresql_stat = PostgreSQLStatistics(stat_title=body.title_statistics,
                                           username=body.username,
                                           tokenconf=body.token,
                                           set_of_test_types=body.set_of_test_types,
                                           comparison_list=body.comparison_list,
                                           comparison_kernel_list=body.comparison_kernel_list)
    response = {
        "status": "",
        "message": ""
    }
    try:
        postgresql_stat.create()
        response['status'] = "success"
        response["message"] = "Все прошло успешно"
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        postgresql_stat.create()
        response["status"] = "warning"
        response["message"] = "Все должно было отработать но была ошибка ApiPermissionError, после нее создание статистики было вызвано повторно"
    except Exception as error:
        message_error = UtilGetTraceback.get_traceback(e=error)
        response["status"] = "error"
        response["message"] = message_error
    return response

@app.post('/docker-statistics')
def docker_statistics(body: Statistics):
    docker_stat = DockerStatstics(stat_title=body.title_statistics,
                                   username=body.username,
                                   tokenconf=body.token,
                                   set_of_test_types=body.set_of_test_types,
                                   comparison_list=body.comparison_list,
                                   comparison_kernel_list=body.comparison_kernel_list,)
    response = {
        "status": "",
        "message": ""
    }
    try:
        docker_stat.create()
        response['status'] = "success"
        response["message"] = "Все прошло успешно"
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        docker_stat.create()
        response["status"] = "warning"
        response["message"] = "Все должно было отработать но была ошибка ApiPermissionError, после нее создание статистики было вызвано повторно"
    except Exception as error:
        message_error = UtilGetTraceback.get_traceback(e=error)
        response["status"] = "error"
        response["message"] = message_error
    return response

@app.post('/file-systems-statistics')
def file_systems_statistics(body: Statistics):
    file_systems_stat = FileSystemStatistics(stat_title="Файловые системы",
                                             username=body.username, 
                                             tokenconf=body.token,
                                             set_of_test_types={"EXFAT", "EXT2", "EXT4", "EXT4 parsec", "FAT", "NTFS", "XFS", "XFS parsec", "OCFS2", "CEPH", "CEPH fio"},
                                             comparison_list=[["EXT4", "XFS"], ["EXT4", "EXT4 parsec"]])
    response = {
        "status": "",
        "message": ""
    }
    try:
        file_systems_stat.create()
        response["message"] = "Файловые системы - Все прошло успешно"
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        file_systems_stat.create()
        response["status"] = "warning"
        response["message"] = "Файловые системы - Все должно было отработать но была ошибка ApiPermissionError, после нее создание статистики было вызвано повторно"
    except Exception as error:
        message_error = UtilGetTraceback.get_traceback(e=error)
        response["status"] = "were_errors"
        response["message"] = f"\nФайловые системы - {message_error}\n"


@app.post("/all-statistics")
def all_statistics(body: Auth):
    response = {
        "status": "success",
        "message": []
    }
    unix_stat = BaseStatistics(stat_title="UnixBench", 
                               username=body.username, 
                               tokenconf=body.token,
                               set_of_test_types={"unix", "unix parsec"},
                               comparison_list=[["unix", "unix parsec"]]
                               )
    
    try:
        unix_stat.create()
        response["message"].append("UnixBench - Все прошло успешно")
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        unix_stat.create()
        response["status"] = "warning"
        response["message"].append("UnixBench - Все должно было отработать но была ошибка ApiPermissionError, после нее создание статистики было вызвано повторно")
    except Exception as error:
        message_error = UtilGetTraceback.get_traceback(e=error)
        response["status"] = "were_errors"
        response["message"].append(f"\nUnixBench - {message_error}\n")

    system_services_stat = BaseStatistics(stat_title="Системные службы",
                                          username=body.username, 
                                          tokenconf=body.token,
                                          set_of_test_types={"auditd-p", "auditd-f", "auditd-u", "syslog-ng"}
                                          )
    try:
        system_services_stat.create()
        response["message"].append("Системные службы - Все прошло успешно")
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        system_services_stat.create()
        response["status"] = "warning"
        response["message"].append("Системные службы - Все должно было отработать но была ошибка ApiPermissionError, после нее создание статистики было вызвано повторно")
    except Exception as error:
        message_error = UtilGetTraceback.get_traceback(e=error)
        response["status"] = "were_errors"
        response["message"].append(f"\nСистемные службы - {message_error}\n")

    file_systems_stat = BaseStatistics(stat_title="Файловые системы",
                                       username=body.username, 
                                       tokenconf=body.token,
                                       set_of_test_types={"EXFAT", "EXT2", "EXT4", "EXT4 parsec", "FAT", "NTFS", "XFS", "XFS parsec", "OCFS2"},
                                       comparison_list=[["EXT4", "XFS"], ["EXT4", "EXT4 parsec"]])
    try:
        file_systems_stat.create()
        response["message"].append("Файловые системы - Все прошло успешно")
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        file_systems_stat.create()
        response["status"] = "warning"
        response["message"].append("Файловые системы - Все должно было отработать но была ошибка ApiPermissionError, после нее создание статистики было вызвано повторно")
    except Exception as error:
        message_error = UtilGetTraceback.get_traceback(e=error)
        response["status"] = "were_errors"
        response["message"].append(f"\nФайловые системы - {message_error}\n")

    postresql_stat = PostgreSQLStatistics(stat_title="PostgreSQL",
                                          username=body.username, 
                                          tokenconf=body.token,
                                          set_of_test_types={"postgresql", "postgresql-sm", "postgresql-aud-off", "psql parsec", "psql vanilla", "tantor vanilla", "psql balance"},
                                          comparison_list=[
                                              ["postgresql", "postgresql-sm"], 
                                              ["postgresql", "postgresql-aud-off"], 
                                              ["postgresql", "psql parsec"], 
                                              ["postgresql", "psql vanilla"]],
                                          comparison_kernel_list=["postgresql"])
    try:
        postresql_stat.create()
        response["message"].append("PostgreSQL - Все прошло успешно")
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        postresql_stat.create()
        response["status"] = "warning"
        response["message"].append("PostgreSQL - Все должно было отработать но была ошибка ApiPermissionError, после нее создание статистики было вызвано повторно")
    except Exception as error:
        message_error = UtilGetTraceback.get_traceback(e=error)
        response["status"] = "were_errors"
        response["message"].append(f"\nPostgreSQL - {message_error}\n")

    apache_stat = BaseStatistics(stat_title="Apache",
                                 username=body.username, 
                                 tokenconf=body.token,
                                 set_of_test_types={"apache-rp"},
                                 score_parser=ApacheParser)
    try:
        apache_stat.create()
        response["message"].append("Apache - Все прошло успешно")
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        apache_stat.create()
        response["status"] = "warning"
        response["message"].append("Apache - Все должно было отработать но была ошибка ApiPermissionError, после нее создание статистики было вызвано повторно")
    except Exception as error:
        message_error = UtilGetTraceback.get_traceback(e=error)
        response["status"] = "were_errors"
        response["message"].append(f"\nApache - {message_error}\n")

    parsec_stat = ParsecStatistics(stat_title="Parsec",
                                   username=body.username, 
                                   tokenconf=body.token,
                                   set_of_test_types={"parsec impact-fs", "parsec impact-fs aud-off", "digsig-cdt"},
                                   comparison_list=[["parsec impact-fs", "parsec impact-fs aud-off"]],
                                   score_parser=ParsecParser)
    try:
        parsec_stat.create()
        response["message"].append("Parsec - Все прошло успешно")
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        parsec_stat.create()
        response["status"] = "warning"
        response["message"].append("Parsec - Все должно было отработать но была ошибка ApiPermissionError, после нее создание статистики было вызвано повторно")
    except Exception as error:
        message_error = UtilGetTraceback.get_traceback(e=error)
        response["status"] = "were_errors"
        response["message"].append(f"\nParsec - {message_error}\n")

    freeipa_stat = FreeIpaStatistics(stat_title="FreeIPA",
                                     username=body.username, 
                                     tokenconf=body.token,
                                     set_of_test_types={'FreeIPA auth'},
                                     )
    try:
        freeipa_stat.create()
        response["message"].append("FreeIPA - Все прошло успешно")
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        freeipa_stat.create()
        response["status"] = "warning"
        response["message"].append("FreeIPA - Все должно было отработать но была ошибка ApiPermissionError, после нее создание статистики было вызвано повторно")
    except Exception as error:
        message_error = UtilGetTraceback.get_traceback(e=error)
        response["status"] = "were_errors"
        response["message"].append(f"\nFreeIPA - {message_error}\n")

    virt_stat = VirtStatistics(stat_title="Qemu/KVM/Libvirt",
                               username=body.username, 
                               tokenconf=body.token,
                               set_of_test_types={"FIO", "vPingPong", "vUnixBench", "steal time", "steal time-sm"},
                               comparison_list=[["steal time", "steal time-sm"]])
    try:
        virt_stat.create()
        response["message"].append("Qemu/KVM/Libvirt - Все прошло успешно")
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        virt_stat.create()
        response["status"] = "warning"
        response["message"].append("Qemu/KVM/Libvirt - Все должно было отработать но была ошибка ApiPermissionError, после нее создание статистики было вызвано повторно")
    except Exception as error:
        message_error = UtilGetTraceback.get_traceback(e=error)
        response["status"] = "were_errors"
        response["message"].append(f"\nQemu/KVM/Libvirt - {message_error}\n")

    return response

@app.get("/logs")
def read_logs(last_lines: Optional[int] = Query(None, description="Number of last lines to read"), reverse: bool = False, debug: bool = False, logs_download: bool = False):
    filename = "main_statistics_logger.log" if not debug else "debug_statistics_logger.log"
    logs = UtilForReadLogs.read_last_lines(filename, last_lines, reverse=reverse)
    
    if logs_download:
        if os.path.exists(filename):
            return FileResponse(filename, media_type='application/octet-stream', filename="logs.txt")
        else:
            return {"error": "File not found"}
    
    style = """
    <style>
        body {
            background-color: #222128;
            color: white;
        }
    </style>
    """

    content = f'<html><head>{style}</head><body>'
    for line in logs:
        if "CRITICAL" in line or "ERROR" in line:
            content += f'<div style="color:red;">{line}</div>'
        elif "WARNING" in line:
            content += f'<div style="color:yellow;">{line}</div>'
        else:
            content += f'<p>{line}</p>'
    content += '</body></html>'

    return HTMLResponse(content=content)