from fastapi import FastAPI
from utils import fetch_repository, filter_repository, UtilGetTraceback
from repository_analyzer import RepositoryConfig, RepositoryAnalysisController

app = FastAPI()

@app.get("/get_components_for_testrun_by_changelog")
def get_components_for_testrun_by_changelog(astra_linux_build_version: str, first_level_dependencies: bool = True, return_dct_component_with_packages: bool = False):
    response = {
        "status": "",
        "result": ""
    }
    try:
        repos = fetch_repository(version=astra_linux_build_version)
        filtered_repo = filter_repository(repos=repos)

        controller = RepositoryAnalysisController()
        for repo in filtered_repo:
            repo_config = RepositoryConfig(repo_url=repo, tables_to_process=['Added_binaries', 'Changelog', 'Upgraded_binaries', 'Обновленные бинарные пакеты', 'Изменения в пакетах (changelog для обновленных пакетов)', 'Обновленные бинарные пакеты'])
            controller.add_repository(repo_config)
        
        results = controller.run_analysis(include_components=first_level_dependencies, ret_groups_with_pkgs=return_dct_component_with_packages)
        response['status'] = "success"
        response['result'] = results
        # return results
    except Exception as err:
        message_error = UtilGetTraceback.get_traceback(e=err)
        response["status"] = "error"
        response["result"] = message_error
    return response
