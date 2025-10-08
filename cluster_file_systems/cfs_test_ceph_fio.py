import argparse

from libs.libcfs import check_output_command
from cfs_conf import STORAGE_MOUNT_DIR

class CephFIOTest:

    def __init__(self, abv):
        self.size = "20G"
        self.directory = STORAGE_MOUNT_DIR
        self.runtime = 300 # в секундах
        self.blocksize = "4k"
        self.report_file = "/var/tmp/report/report_fio.txt"
        self.abv = abv
        if self.abv.startswith("1.8"):
            check_output_command(command="dpkg -i /var/tmp/fio/fio_3.33-3_amd64.deb")
        else:
            check_output_command(command="dpkg -i /var/tmp/fio/fio_3.12-2_amd64.deb")

    def run_test(self):
        com_test = f"sudo fio --directory={self.directory} --direct=1 --rw=randrw --bs={self.blocksize} --ioengine=libaio --iodepth=256 --size={self.size} --runtime={self.runtime} --numjobs=2 --time_based --group_reporting --name=iops-qateam13-job --eta-newline=1 > {self.report_file}"
        check_output_command(command=com_test)


if __name__ == "__main__":
    DESCRIPTION = ""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('-abv', 
                    action='store',
                    required=True,
                    help='astra build version',
                    dest='ABV')
    args = parser.parse_args()

    fio_test = CephFIOTest(abv=args.ABV)
    fio_test.run_test()