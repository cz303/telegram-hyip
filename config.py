_ETH_ADDRESS = '(0x57a3e009ad125d5fcf1a800bec2115f2a8096d0b)'
_DB_NAME = 'ascension.db'
_SUPPORT_ACCOUNT = '@master_long'
DEBUG = True


def get_support_account():
    return _SUPPORT_ACCOUNT


def db_name():
    return _DB_NAME


def project_eth_address():
    return _ETH_ADDRESS.lower()
