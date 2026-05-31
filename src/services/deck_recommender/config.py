from utils import *

CONFIG = {}
CONFIG_PATH = pjoin(os.path.dirname(os.path.abspath(__file__)), 'config.yaml')
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        CONFIG = yaml.safe_load(f)
else:
    log(f"未找到配置文件 {CONFIG_PATH}，使用默认配置")

HOST = CONFIG.get('host', '127.0.0.1')
PORT = CONFIG.get('port', 45556)
WORKER_NUM = CONFIG.get('worker_num', 1)
DATA_DIR = CONFIG.get('data_dir', 'lunabot_deckrec_data')
USERDATA_CACHE_NUM = CONFIG.get('userdata_cache_num', 10)
DB_PATH = pjoin(DATA_DIR, 'deckrec.json')
FORCE_SYNC_INTERVAL_HOURS = CONFIG.get('force_sync_interval_hours', 2)
