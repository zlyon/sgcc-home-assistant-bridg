import json
import logging
from datetime import datetime
from typing import Any, Optional

from config import FetcherConfig
from model import AccountData, DailyReading, MonthlyReading, mask_account_no

try:
    import paho.mqtt.client as mqtt
except Exception:  # pragma: no cover - exercised only when dependency is absent
    mqtt = None


class MqttPublisher:
    """Home Assistant MQTT Discovery publisher for SGCC account data."""

    def __init__(self, config: Optional[FetcherConfig] = None):
        self.config = config or FetcherConfig.from_env()
        self.host = self.config.MQTT_HOST
        self.port = self.config.MQTT_PORT
        self.username = self.config.MQTT_USERNAME
        self.password = self.config.MQTT_PASSWORD
        self.discovery_prefix = self.config.MQTT_DISCOVERY_PREFIX or "homeassistant"
        self.client = None
        self.connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.disconnect()

    def connect(self) -> bool:
        if mqtt is None:
            logging.warning("paho-mqtt 未安装，跳过 MQTT 发布。")
            return False
        if not self.host:
            logging.warning("MQTT_HOST 未配置，跳过 MQTT 发布。")
            return False

        try:
            try:
                self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            except Exception:
                self.client = mqtt.Client()
            if self.username:
                self.client.username_pw_set(self.username, self.password or None)
            rc = self.client.connect(self.host, self.port, keepalive=60)
            if rc != 0:
                logging.warning(f"MQTT 连接失败，返回码: {rc}")
                self.connected = False
                return False
            self.client.loop_start()
            self.connected = True
            return True
        except Exception as e:
            logging.warning(f"MQTT 连接失败: {e}")
            self.connected = False
            return False

    def disconnect(self) -> None:
        if self.client is None:
            return
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception as e:
            logging.warning(f"MQTT 断开连接失败: {e}")
        finally:
            self.connected = False

    def publish_account_data(self, account_data: AccountData) -> bool:
        if self.client is None or not self.connected:
            logging.warning("MQTT 未连接，跳过发布。")
            return False

        try:
            account_no = account_data.account.account_no
            masked = mask_account_no(account_no)
            node = self._safe_topic_part(f"sgcc_{masked}")
            device = {
                "identifiers": [f"sgcc_{masked}"],
                "name": f"国网电费 {masked}",
                "manufacturer": "SGCC bridge",
            }

            published = 0
            for spec in self._sensor_specs(account_data, masked, device):
                value = spec.pop("value")
                if value is None:
                    continue
                key = spec.pop("key")
                state_topic = f"sgcc/{node}/{key}/state"
                config_topic = f"{self.discovery_prefix}/sensor/{node}/{key}/config"
                payload = {
                    "name": spec.pop("name"),
                    "unique_id": f"sgcc_{masked}_{key}",
                    "state_topic": state_topic,
                    "device": device,
                }
                payload.update({k: v for k, v in spec.items() if v is not None})
                self._publish(config_topic, json.dumps(payload, ensure_ascii=False), retain=True)
                self._publish(state_topic, self._format_value(value), retain=False)
                published += 1
            return published > 0
        except Exception as e:
            logging.warning(f"MQTT 发布失败: {e}")
            return False

    def _publish(self, topic: str, payload: Any, retain: bool = False) -> None:
        result = self.client.publish(topic, payload=payload, retain=retain)
        rc = getattr(result, "rc", 0)
        if rc != 0:
            raise RuntimeError(f"publish rc={rc} topic={topic}")

    def _sensor_specs(self, account_data: AccountData, masked: str, device: dict) -> list[dict]:
        balance = account_data.balance
        latest_daily = self._latest_daily(account_data.daily)
        latest_monthly = self._latest_monthly(account_data.monthly)
        yearly = account_data.yearly
        month_tou = self._month_tou(account_data.daily)

        return [
            {
                "key": "balance",
                "name": f"电费余额 {masked}",
                "value": balance.balance_cny if balance else None,
                "unit_of_measurement": "CNY",
                "device_class": "monetary",
                "state_class": "total",
            },
            {
                "key": "prepay_balance",
                "name": f"预付费余额 {masked}",
                "value": balance.prepay_balance_cny if balance else None,
                "unit_of_measurement": "CNY",
                "device_class": "monetary",
                "state_class": "total",
            },
            {
                "key": "arrears",
                "name": f"应交金额 {masked}",
                "value": balance.arrears_cny if balance else None,
                "unit_of_measurement": "CNY",
                "device_class": "monetary",
                "state_class": "total",
            },
            {
                "key": "last_daily_usage",
                "name": f"最近日用电 {masked}",
                "value": latest_daily.total_usage_kwh if latest_daily else None,
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "measurement",
            },
            {
                "key": "month_usage",
                "name": f"月度用电 {masked}",
                "value": latest_monthly.total_usage_kwh if latest_monthly else None,
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "measurement",
            },
            {
                "key": "month_charge",
                "name": f"月度电费 {masked}",
                "value": latest_monthly.total_charge_cny if latest_monthly else None,
                "unit_of_measurement": "CNY",
                "device_class": "monetary",
                "state_class": "measurement",
            },
            {
                "key": "month_valley",
                "name": f"月度谷时电量 {masked}",
                "value": month_tou["valley"],
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "measurement",
            },
            {
                "key": "month_flat",
                "name": f"月度平时电量 {masked}",
                "value": month_tou["flat"],
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "measurement",
            },
            {
                "key": "month_peak",
                "name": f"月度峰时电量 {masked}",
                "value": month_tou["peak"],
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "measurement",
            },
            {
                "key": "month_tip",
                "name": f"月度尖时电量 {masked}",
                "value": month_tou["tip"],
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "measurement",
            },
            {
                "key": "year_usage",
                "name": f"年度用电 {masked}",
                "value": yearly.total_usage_kwh if yearly else None,
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "total_increasing",
            },
            {
                "key": "year_charge",
                "name": f"年度电费 {masked}",
                "value": yearly.total_charge_cny if yearly else None,
                "unit_of_measurement": "CNY",
                "device_class": "monetary",
                "state_class": "total_increasing",
            },
        ]

    @staticmethod
    def _latest_daily(rows: list[DailyReading]) -> Optional[DailyReading]:
        dated = [row for row in rows if row.date]
        return max(dated, key=lambda row: row.date) if dated else None

    @staticmethod
    def _latest_monthly(rows: list[MonthlyReading]) -> Optional[MonthlyReading]:
        dated = [row for row in rows if row.year_month]
        return max(dated, key=lambda row: row.year_month) if dated else None

    @staticmethod
    def _month_tou(rows: list[DailyReading]) -> dict[str, Optional[float]]:
        current_month_prefix = datetime.now().strftime("%Y-%m")
        current_rows = [row for row in rows if str(row.date or "")[:7] == current_month_prefix]
        if not current_rows:
            return {"valley": None, "flat": None, "peak": None, "tip": None}
        return {
            "valley": sum(row.valley_usage_kwh or 0 for row in current_rows),
            "flat": sum(row.flat_usage_kwh or 0 for row in current_rows),
            "peak": sum(row.peak_usage_kwh or 0 for row in current_rows),
            "tip": sum(row.tip_usage_kwh or 0 for row in current_rows),
        }

    @staticmethod
    def _safe_topic_part(value: str) -> str:
        return value.replace("*", "x").replace("/", "_").replace(" ", "_")

    @staticmethod
    def _format_value(value: Any) -> str:
        if isinstance(value, float):
            return str(round(value, 4))
        return str(value)
