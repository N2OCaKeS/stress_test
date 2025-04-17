from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from time import sleep
from sys import exit
import shutil
import pathlib
from os import path
if path.isfile('/home/u/ilo.py'):
    shutil.copyfile('/home/u/ilo.py', str(pathlib.Path(__file__).parent / 'ilo.py'))
    #from libs.ilo import stand3, stand4
    stand3 = {}
    stand4 = {}
else:
    print('Not exist settnigs ilo file')
    exit(2)
    

class iLOConsoleCaller:

    def __init__(self,
                 stand_number=None):
        
        if stand_number == 'stand3':
            self.stand = stand3
        elif stand_number == 'stand4':
            self.stand = stand4

    def ilo_console_loader(self):
        url = self.stand['url']
        username = self.stand['username']
        password = self.stand['password']

        driver = webdriver.Firefox(service=Service(f'{pathlib.Path(__file__).parent}/drivers/geckodriver')) 
        driver.get(url)
        driver.switch_to.frame("appFrame")

        if self.stand == stand3:
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, 'usernameInput'))).send_keys(username)
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, 'passwordInput'))).send_keys(password)
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, 'ID_LOGON'))).click()
        elif self.stand == stand4:
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, 'username'))).send_keys(username)
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, 'password'))).send_keys(password)
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, 'login-form__submit'))).click()

        sleep(8)
        #frames = driver.find_elements_by_tag_name("iframe")
        #for frame in frames:
        #    print(frame.get_attribute('id'))

        if self.stand == stand3:
            driver.switch_to.frame("frameContent")
            sleep(2)
            driver.switch_to.frame("iframeContent")
        elif self.stand == stand4:
            driver.switch_to.frame("iframeContent")

        html5 = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, "//a/span[contains(text(), 'HTML5')]")))
        html5.click()
        sleep(5)

        #driver.close()