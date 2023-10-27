import sys
import logging
import uvicorn

logging.basicConfig(filename='/home/u/git/stress_test/bendiks_app/error.log',
                    format='%(asctime)s - %(levelname)s - %(funcName)s: %(lineno)d - %(message)s',
                    datefmt='%Y/%m/%d %H:%M:%S',
                    filemode='a')
sys.path.insert(0,"/home/u/git/stress_test/bendiks_app")

from test_fastapi import app

if __name__ == "__main__":
    uvicorn.run(app)

