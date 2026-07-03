import os

from .llm_config import load_llm_config

# 国网电力官网
LOGIN_URL = "https://95598.cn/osgweb/login"
ELECTRIC_USAGE_URL = "https://95598.cn/osgweb/electricityCharge"
BALANCE_URL = "https://95598.cn/osgweb/userAcc"
BILL_SUMMARY_URL = "https://95598.cn/osgweb/electricityCharge"
STEP_ELECTRICITY_URL = "https://95598.cn/osgweb/stepElectricityConsumption"
ELECTRIC_BILL_SUMMARY_URL = (
    "https://95598.cn/osgweb01/electricityChargeQuery/queryElectricBillSummary"
)

# Home Assistant
SUPERVISOR_URL = "http://supervisor/core"
API_PATH = "/api/states/"

BALANCE_SENSOR_NAME = "sensor.electricity_charge_balance"
DAILY_USAGE_SENSOR_NAME = "sensor.last_electricity_usage"
YEARLY_USAGE_SENSOR_NAME = "sensor.yearly_electricity_usage"
YEARLY_CHARGE_SENSOR_NAME = "sensor.yearly_electricity_charge"
MONTH_USAGE_SENSOR_NAME = "sensor.month_electricity_usage"
MONTH_CHARGE_SENSOR_NAME = "sensor.month_electricity_charge"
MONTH_VALLEY_SENSOR_NAME = "sensor.month_valley_usage"
MONTH_FLAT_SENSOR_NAME = "sensor.month_flat_usage"
MONTH_PEAK_SENSOR_NAME = "sensor.month_peak_usage"
MONTH_TIP_SENSOR_NAME = "sensor.month_tip_usage"
PREPAY_BALANCE_SENSOR_NAME = "sensor.prepay_balance"
ARREARS_SENSOR_NAME = "sensor.electricity_arrears"
BALANCE_UNIT = "CNY"
USAGE_UNIT = "KWH"

_LLM_CONFIG = load_llm_config()
LLM_API_KEY = _LLM_CONFIG.api_key
LLM_BASE_URL = _LLM_CONFIG.base_url
LLM_MODEL = _LLM_CONFIG.model


def get_data_dir() -> str:
    """获取数据存储目录：Docker 用 /data，本地用项目下的 data/"""
    if 'PYTHON_IN_DOCKER' in os.environ:
        return '/data'
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
    os.makedirs(data_dir, exist_ok=True)
    return data_dir
