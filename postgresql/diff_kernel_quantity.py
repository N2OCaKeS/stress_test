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

args = parser.parse_args()

test = BaseTest(database='psql',
                storage_device='NVME',
                stand_number=args.STAND)

data = {
    'Kernels':str(args.KERNELS_QUANTITY),
    'tps':test.test_run(clients=800, 
                        repeat=20)
}

results = pd.DataFrame(data)
print(results)

if not path.isfile('results.csv'):
    results.to_csv('results.csv')
else:
    results = pd.read_csv('results.csv', delimiter=',')
    print(results)
    results = pd.concat([results, data], ignore_index=True)
    print(results)

