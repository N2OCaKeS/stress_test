import argparse

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)


parser.add_argument('-tt', '--type_test',
                    action='store',
                    # required=True,
                    help='type test',
                    dest='TTEST')

args = parser.parse_args()


if args.TTEST == "check_write_logs":
    pass
else:
    pass