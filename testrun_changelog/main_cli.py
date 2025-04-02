import argparse
from utils import fetch_repository, filter_repository
from repository_analyzer import RepositoryConfig, RepositoryAnalysisController

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('-fld', '--dependencies',
                    action='store_true',
                    required=False,
                    default=False,
                    help='first level dependencies',
                    dest='FLD')
parser.add_argument('-vers', '--version',
                    action='store',
                    required=True,
                    help='Astra Linux build version',
                    dest='ALVERS')
args = parser.parse_args()

if __name__ == "__main__":
    repos = fetch_repository(version=args.ALVERS)
    filtered_repo = filter_repository(repos=repos)

    controller = RepositoryAnalysisController()
    for repo in filtered_repo:
        repo_config = RepositoryConfig(repo_url=repo, tables_to_process=['Added_binaries', 'Changelog', 'Upgraded_binaries'])
        controller.add_repository(repo_config)
    
    results = controller.run_analysis(include_components=args.FLD)
    print(results)