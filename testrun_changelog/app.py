from fastapi import FastAPI
from utils import fetch_repository, filter_repository
from repository_analyzer import RepositoryConfig, RepositoryAnalysisController

app = FastAPI()

@app.get("/get_components_for_testrun_by_changelog")
def get_components_for_testrun_by_changelog(astra_linux_build_version: str, first_level_dependencies: bool = True):
    repos = fetch_repository(version=astra_linux_build_version)
    filtered_repo = filter_repository(repos=repos)

    controller = RepositoryAnalysisController()
    for repo in filtered_repo:
        repo_config = RepositoryConfig(repo_url=repo, tables_to_process=['Added_binaries', 'Changelog', 'Upgraded_binaries'])
        controller.add_repository(repo_config)

    results = controller.run_analysis(include_components=first_level_dependencies)
    
    return results