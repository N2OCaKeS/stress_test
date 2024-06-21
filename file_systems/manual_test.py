import subprocess
import argparse
import os
from libs.libtable import Report
from fsb_conf import INODE_COUNT, STORAGE_MOUNT_DIR, FILES, FILES_LIMIT, \
                     FILES_STEP, SCRIPT_DIR, REPORT_PATH, REPORT_FILENAME


parser = argparse.ArgumentParser()
parser.add_argument('-fs',
                    action='store',
                    choices=['ext2',
                             'ext3',
                             'ext4',                             
                             'xfs'],
                    required=False,
                    help='filesystem',
                    dest='FS')

parser.add_argument('-test',
                    action='store_true',
                    required=False,
                    help='start test',
                    dest='TEST')

parser.add_argument('-report',
                    action='store_true',
                    required=False,
                    help='make report',
                    dest='REPORT')

args = parser.parse_args()


def cmd(command):
    return subprocess.run(command, shell=True).returncode


def check_output_command(command, out=None):
    result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    output, errors = result.communicate()
    output = os.linesep.join([s for s in output.splitlines() if s])
    errors = os.linesep.join([s for s in errors.splitlines() if s])
    if errors == "":
        return output
    elif out != None:
        return errors + output
    else:
        return errors


def set_fs():
    storage_name = check_output_command("lsblk | awk 'NR==2' | awk '{print $1;}'")
    print(storage_name)

    if cmd(f'lsblk | grep {storage_name}') == 0:
        if cmd(f'lsblk | grep {storage_name}1') == 0:
            cmd('umount /mnt')
            cmd(f'sudo parted -s /dev/{storage_name} select && sudo parted -s /dev/{storage_name} rm 1')

    if args.FS == 'xfs':
        cmd(f'sudo parted -s /dev/{storage_name} mklabel gpt mkpart primary xfs 0% 100%')
        cmd(f"sudo mkfs -t {args.FS} -f /dev/{storage_name}1")
    else:
        cmd(f'sudo parted -s /dev/{storage_name} mklabel gpt mkpart primary {args.FS} 0% 100%')
        cmd(f"sudo mkfs -t {args.FS} {INODE_COUNT} -F /dev/{storage_name}1")

    cmd(f"mount /dev/{storage_name}1 {STORAGE_MOUNT_DIR}")


def fs_mark33_count(start=FILES,
                    end=FILES_LIMIT, 
                    step=FILES_STEP, 
                    size=1024, 
                    mount_dir=STORAGE_MOUNT_DIR, 
                    scr_dir=SCRIPT_DIR, 
                    fat32=False):
    
    print(f"# TEST # <{fs_mark33_count.__name__}>:")

    if not os.path.isdir(REPORT_PATH):
        os.mkdir(REPORT_PATH)
    report_file = open('{}/{}'.format(REPORT_PATH, REPORT_FILENAME), 'w')
    report_file.close()




    #TODO: Add original mark3.3 without parsec librares

    if fat32:
        run_fs_mark = '{script_dir}/fs_mark-3.3/fs_mark_original -d {test_dir} -s {file_size} -n {file_count} -v -D 20 -N 10000'
    else:
        run_fs_mark = '{script_dir}/fs_mark-3.3/fs_mark_original -d {test_dir} -s {file_size} -n {file_count} -v'
    print('FSUse%        Count         Size    Files/sec     App Overhead        CREAT (Min/Avg/Max)        WRITE (Min/Avg/Max)        FSYNC (Min/Avg/Max)         SYNC (Min/Avg/Max)        CLOSE (Min/Avg/Max)       UNLINK (Min/Avg/Max)')
    for count in range(start, end, step):
        test = subprocess.run(run_fs_mark.format(script_dir=scr_dir,
                                                 test_dir=mount_dir,
                                                 file_size=str(size),
                                                 file_count=str(count)),
                                shell=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        out = os.linesep.join([s for s in test.stdout.decode("utf-8").splitlines() if s])
        err = os.linesep.join([s for s in test.stdout.decode("utf-8").splitlines() if s])

        if test.returncode == 0:
            with open('{}/{}'.format(REPORT_PATH, REPORT_FILENAME), 'a+') as report_file:
                report_file.write(out.splitlines()[-1]+'\n')
            print("{} | \033[92mpass\033[0m".format(out.splitlines()[-1]))
            print(out)
        else:
            print('{} | \033[91mfail\033[0m'.format(err))
            print(err)
            return False


def report():
    report = Report(ox_lo_lim=FILES,
                    ox_step=FILES_STEP,
                    ox_up_lim=FILES_LIMIT)
    report.create_beauty_table()
    report.create_fsb_fc_sp_graph()
    report.create_fsb_fc_app_overhead_graph()
    report.create_fsb_fc_create_graph()
    report.create_fsb_fc_write_graph()
    report.create_fsb_fc_fsync_graph()
    report.create_fsb_fc_sync_graph()
    report.create_fsb_fc_close_graph()
    report.create_fsb_fc_unlink_graph()
    report.merge(ox_lst=report.file_count_lst,
                 table_lst=['fsb_report_table.html'],
                 graph_lst=['fsb_file_count_speed_graph.png',
                            'fsb_file_count_app_overhead_graph.png',
                            'fsb_file_count_create_graph.png',
                            'fsb_file_count_write_graph.png',
                            'fsb_file_count_fsync_graph.png',
                            'fsb_file_count_sync_graph.png',
                            'fsb_file_count_close_graph.png',
                            'fsb_file_count_unlink_graph.png'])



if __name__ == '__main__':
    if args.FS:
        set_fs()
    elif args.TEST:
        fs_mark33_count()
    elif args.REPORT:
        report()

