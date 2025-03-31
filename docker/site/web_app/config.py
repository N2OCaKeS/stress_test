import os

class Config(object):
    APPNAME = 'web_app'
    ROOT = os.path.abspath(APPNAME)
    WEB_UPLOAD = '/static/upload'
    SERVER_PATH = os.path.join(ROOT, WEB_UPLOAD)




