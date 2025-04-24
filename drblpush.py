import sys
import pexpect
from conf import PATH_MACADDRS, FIRST_ADDRESS_IN_LAST_OCTET, PATH_IMAGES

def automate_drblpush():
    try:
        child = pexpect.spawn('sudo', ['drblpush', '-i'], env={'LC_ALL': 'C.UTF-8'})
        child.logfile = sys.stdout.buffer

        child.expect(r'.*Please enter DNS domain.*')
        # child.expect(r'\[drbl.org\]')
        child.sendline('astralinux.ru')

        child.expect(r'.*Please enter NIS/YP domain name.*')
        child.sendline('')

        child.expect(r'.*Please enter the client hostname prefix.*')
        child.sendline('')

        child.expect(r'.*Available ethernet ports in this server:.*')
        child.sendline('')

        child.expect('Are you sure you want to continue?')
        child.expect(r'\[y/N\]')
        child.sendline('y')

        child.expect(r'.*Do you want to try it again?.*\[Y/n\]', timeout=300)
        child.sendline('n')

        child.expect(r'.*Do you want to let the DHCP service in DRBL server offer same IP address.*\[y/N\]')
        child.sendline('y')

        child.expect(r'.*OK! Please tell me the file name which contains the MAC address of clients line*.')
        child.sendline(f"{PATH_MACADDRS}")

        child.expect(r'.*What is the initial number do you want to use in the last set of digits in the IP.*')
        child.sendline(f'{FIRST_ADDRESS_IN_LAST_OCTET}')

        child.expect(r'.*We will set the IP address for the clients connected.*Accept.*')
        child.sendline('y')

        child.expect(r'.*Press Enter to continue.*')
        child.sendline('')

        child.expect(r'.*In the system, there are 3 modes for diskless linux services.*')
        child.sendline('2')

        child.expect(r'.*In the system, there are 4 modes available for clonezilla:.*')
        child.sendline('0')

        child.expect(r'.*When using clonezilla, which directory in this server you want to store the saved image.*')
        child.sendline(f"{PATH_IMAGES}")

        child.expect(r'.*Do you want to set the pxelinux password for clients.*')
        child.sendline('n')

        child.expect(r'.*Do you want to set the boot prompt for clients?.*')
        child.sendline('y')

        child.expect(r'.*sec is the boot prompt timeout for clients?.*')
        child.sendline('')

        child.expect('Do you want to use graphic background for PXE menu when client boots?')
        child.expect(r'\[y/N\]')
        child.sendline('N')

        child.expect(r'.*Do you want to let DRBL server as a NAT server?.*')
        child.sendline('y')

        # child.expect(r'.*Do you want to keep the old setting of existing DRBL clients if they exist?.*')
        # child.sendline('n')
        
        # child.expect(r'.*Press Enter.*')
        # child.sendline('')

        index = child.expect([
            r'.*Do you want to keep the old setting of existing DRBL clients if they exist?.*',
            r'.*Press Enter.*'
        ], timeout=60)
        if index == 0:
            child.sendline('n')
        else:
            child.sendline('')

        child.expect(r'.*Do you want to continue?.*\[Y/n\]')
        child.sendline('y')

        child.expect(pexpect.EOF, timeout=300)
        print("\nПроцесс настройки DRBL успешно завершен!")

    except pexpect.EOF:
        print("Ошибка: неожиданное завершение программы.")
    except pexpect.TIMEOUT:
        print("Ошибка: превышено время ожидания.")
    except Exception as e:
        print(f"Произошла ошибка: {str(e)}")

if __name__ == "__main__":
    automate_drblpush()