# import sys
# import os
# sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
# sys.path.insert(0, "/home/u/git/stress_test/docker/site")

from web_app import create_app

application = create_app()

# if __name__ == "__main__":
#     application.run()
