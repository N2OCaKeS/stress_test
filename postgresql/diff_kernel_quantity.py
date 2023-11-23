import argparse
from libs.libpsb import BaseTest
import pandas as pd
from os import path

parser = argparse.ArgumentParser()
parser.add_argument('-q',
                    action='store',
                    required=True,
                    help='kernels quantity',
                    dest='KERNELS_QUANTITY')

parser.add_argument('-st',
                    action='store',
                    required=True,
                    help='stand number',
                    dest='STAND')

parser.add_argument('-cl',
                    action='store',
                    required=False,
                    help='calculate results',
                    dest='CALC')

args = parser.parse_args()

test = BaseTest(database='psql',
                storage_device='NVME',
                stand_number=args.STAND)

def dates_prepare():
    data = {
            'Kernels':str(args.KERNELS_QUANTITY),
            'tps':test.test_run(clients=800, 
                                repeat=20)
            }

    return data

if not path.isfile('results.csv'):
    results = pd.DataFrame(dates_prepare(), index=[0])
    print(results)
    results.to_csv('results.csv', index=False)
else:
    results_from_csv = pd.read_csv('results.csv', delimiter=',')
    print(results_from_csv)
    test.prepare = False
    results = pd.DataFrame(dates_prepare(), index=[0])
    results = pd.concat([results_from_csv, results], ignore_index=True)
    print(results)
    results.to_csv('results.csv', index=False)


