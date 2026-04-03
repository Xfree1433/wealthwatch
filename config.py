"""
WealthWatch Configuration
"""
import os
import secrets


class BaseConfig:
    SECRET_KEY = os.environ.get('WW_SECRET_KEY', secrets.token_hex(32))
    DATABASE = None  # Set dynamically from data_dir()


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class ProductionConfig(BaseConfig):
    DEBUG = False


config_map = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': ProductionConfig,
}
