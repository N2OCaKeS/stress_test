#!/bin/python3

import subprocess




subprocess.run('./backup_image.py -sn 1 -rs 1.7.4 -test XFS -mode orel -kn 5.15.0-70-generic -stand stand1 -tcyc 1.7.4_orel_5.15.0-70-generic_stand1 -tcas file system benchmark. XFS -branch file_systems -cti 2773', shell=True)

