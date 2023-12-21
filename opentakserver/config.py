import ssl

from secret_key import *
from pathlib import Path
import os
from flask_security import uia_username_mapper


class Config:
    DATA_FOLDER = os.path.join(Path.home(), 'ots')
    HTTP_PORT = 8080
    HTTPS_PORT = 8443
    COT_PORT = 8087
    COT_STREAMING_PORT = 8088
    COT_SSL_PORT = 8089
    SECRET_KEY = secret_key
    VERSION = '0.1-OTS-DEV'
    OTS_SSL_VERIFICATION_MODE = ssl.CERT_OPTIONAL  # https://docs.python.org/3/library/ssl.html#ssl.SSLContext.verify_mode

    # flask-sqlalchemy
    SQLALCHEMY_DATABASE_URI = "sqlite:////{}".format(os.path.join(DATA_FOLDER, 'ots.db'))
    SQLALCHEMY_ECHO = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_RECORD_QUERIES = True

    ALLOWED_EXTENSIONS = {'zip', 'xml'}
    SERVER_DOMAIN_OR_IP = server_domain_or_ip
    NODE_ID = node_id
    UPLOAD_FOLDER = os.path.join(DATA_FOLDER, 'uploads')
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    CA_FOLDER = os.path.join(DATA_FOLDER, 'ca')
    CERT_PASSWORD = 'atakatak'
    CA_EXPIRATION_TIME = 3650  # In days, defaults to 10 years

    # Flask-Security-Too
    SECURITY_PASSWORD_SALT = security_password_salt
    REMEMBER_COOKIE_SAMESITE = "strict"
    SESSION_COOKIE_SAMESITE = "strict"
    SECURITY_USER_IDENTITY_ATTRIBUTES = [{"username": {"mapper": uia_username_mapper, "case_insensitive": True}}]
    SECURITY_USERNAME_ENABLE = True
    SECURITY_USERNAME_REQUIRED = True
    SECURITY_TRACKABLE = True
    SECURITY_CSRF_COOKIE_NAME = "XSRF-TOKEN"
    WTF_CSRF_TIME_LIMIT = None
    SECURITY_CSRF_IGNORE_UNAUTH_ENDPOINTS = True
    WTF_CSRF_CHECK_DEFAULT = False
    SECURITY_RETURN_GENERIC_RESPONSES = True
