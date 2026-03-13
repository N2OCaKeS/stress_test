import subprocess, logging
import redfish

from app.api.v1.models.physical_servers import PhysicalServer, DriverType

def __cmd(self, cmd):
    output = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE).stdout.decode("utf-8")
    return output

class ILOController:
    def __init__(self, server: PhysicalServer):
        self.address = server.admin_panel_ip
        self.login = server.admin_panel_user
        self.password = server.admin_panel_pass
        self.no_fprint = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
        self.old_mode_key = '-oKexAlgorithms=+diffie-hellman-group1-sha1'
        self.ssh_command = f'sshpass -p "{self.password}" ssh {self.no_fprint} {self.old_mode_key} -l {self.login} {self.address}'

    def set_boot_order(self):        
        try:
            show_config = 'show /system1/bootconfig1/oemhp_uefibootsource'
            set_new_config = 'set /system1/bootconfig1/oemhp_uefibootsource{} bootorder=1'
            for i in range(0, self.slot_count + 1, 1):
                answer = __cmd(f'{self.ssh_command} {show_config}{i}')
                if self.boot_type in answer and i == 1:
                    logging.debug(f'\033[93m{self.boot_type} загрузка уже в приоритете, настройка не требуется\033[0m\n')
                    break
                elif self.boot_type in answer and i != 1:
                    logging.debug(f'\033[93m{answer}\033[0m')
                    result = __cmd(f'{self.ssh_command} {set_new_config}'.format(i))
                    if 'Bootorder being set' in result:
                        logging.debug(f'\033[92mПриоритет загрузки успешно изменен на {self.boot_type}\033[0m\n')
                    break
        except Exception as e:
            logging.error(f'Type:{type(e).__name__}, \nMessage:{str(e)}')

    def power_on(self):
        subprocess.run(f"{self.ssh_command} power on", shell=True, check=False)

    def power_off(self):
        subprocess.run(f"{self.ssh_command} power off", shell=True, check=False)

    def reboot(self):
        subprocess.run(f"{self.ssh_command} reset /system1", shell=True, check=False)

class IDRACController:
    def __init__(self, server: PhysicalServer):
        self.address = server.admin_panel_ip
        self.login   = server.admin_panel_user
        self.password= server.admin_panel_pass
        self.client  = redfish.redfish_client(
            base_url=self.address,
            username=self.login,
            password=self.password,
        )      

    def _login(self):
        self.client.login(auth="session")

    def _logout(self):
        self.client.logout()

    def set_boot_order(self):
        self._login

        try:
            response = self.client.get('/redfish/v1/Systems/System.Embedded.1')
            system_info = response.dict
            logging.debug("System Information: ", system_info)

            response = self.client.get('/redfish/v1/Systems/System.Embedded.1/BootSources')
            boot_sources = response.dict
            logging.debug("Boot Sources: ", boot_sources)

            body = {
                "Boot": {
                    "BootSourceOverrideTarget": "Pxe",
                    "BootSourceOverrideEnabled": "Once"
                }
            }
            response = self.client.patch('/redfish/v1/Systems/System.Embedded.1', body=body)
            if response.status == 200:
                logging.debug(f'\033[92mПриоритет загрузки успешно изменен на {self.boot_type}\033[0m\n')
        except Exception as e:
            logging.error(f'Type:{type(e).__name__}, \nMessage:{str(e)}')
        finally:
            self._logout

    def power_on(self):
        self._login()
        try:
            body = {
                "ResetType": "ForceOff"
            }
            response = self.client.post('/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset', body=body)
            if response.status in [200, 204]:
                logging.debug('execute IPMI iDRAC power on successfully')
            else:
                logging.error(f'execute IPMI iDRAC power on failed, status: {response.status}')
        except Exception as e:
            logging.error(f'Type:{type(e).__name__}, \nMessage:{str(e)}')
        finally:
            self.client.logout()
        self._logout()   

    def power_off(self):
        self._login()        
        try:
            body = {
                "ResetType": "ForceOff"
            }
            response = self.client.post('/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset', body=body)
            if response.status in [200, 204]:
                logging.debug('execute IPMI iDRAC hard shutdown successfully')
            else:
                logging.error(f'execute IPMI iDRAC hard shutdown failed, status: {response.status}')
        except Exception as e:
            logging.error(f'Type:{type(e).__name__}, \nMessage:{str(e)}')
        finally:
            self.client.logout()
        self._logout()

    def reboot(self):       
        self._login()        
        try:
            body = {
                "ResetType": "ForceRestart"
            }
            response = self.client.post('/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset', body=body)
            if response.status in [200, 204]:
                logging.debug('execute IPMI iDRAC hard reboot successfully')
            else:
                logging.error(f'execute IPMI iDRAC hard reboot failed, status: {response.status}')
        except Exception as e:
            logging.error(f'Type:{type(e).__name__}, \nMessage:{str(e)}')
        finally:
            self.client.logout()
        self._logout()


class ServerPowerService:
    def __init__(self, server: PhysicalServer):
        self.server = server
        if server.driver_type == DriverType.idrac.value:
            self.ctrl = IDRACController(server)
        else:
            self.ctrl = ILOController(server)

    def power_on(self):
        return self.ctrl.power_on()

    def power_off(self):
        return self.ctrl.power_off()

    def reboot(self):
        return self.ctrl.reboot()
    
    def set_boot_order(self):
        return self.ctrl.set_boot_order()
