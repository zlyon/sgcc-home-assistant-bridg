import json
import unittest
from datetime import datetime
from types import SimpleNamespace

import mqtt_publisher
from config import FetcherConfig
from model import Account, AccountData, Balance, DailyReading, MonthlyReading, YearlyReading
from mqtt_publisher import MqttPublisher


class FakeClient:
    instances = []

    def __init__(self, *args, **kwargs):
        self.published = []
        self.username = None
        self.password = None
        self.connected_to = None
        FakeClient.instances.append(self)

    def username_pw_set(self, username, password=None):
        self.username = username
        self.password = password

    def connect(self, host, port, keepalive=60):
        self.connected_to = (host, port, keepalive)
        return 0

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def publish(self, topic, payload=None, retain=False):
        self.published.append((topic, payload, retain))
        return SimpleNamespace(rc=0)


class MqttPublisherTestCase(unittest.TestCase):
    def setUp(self):
        FakeClient.instances.clear()
        self.original_mqtt = mqtt_publisher.mqtt
        mqtt_publisher.mqtt = SimpleNamespace(
            Client=FakeClient,
            CallbackAPIVersion=SimpleNamespace(VERSION2=object()),
        )

    def tearDown(self):
        mqtt_publisher.mqtt = self.original_mqtt

    def test_publish_account_data_emits_discovery_and_state_without_full_account_no(self):
        current_month = datetime.now().strftime("%Y-%m")
        account_no = "1234567890123"
        cfg = FetcherConfig(
            MQTT_HOST="broker.local",
            MQTT_USERNAME="user",
            MQTT_PASSWORD="pass",
            MQTT_DISCOVERY_PREFIX="homeassistant",
        )
        publisher = MqttPublisher(cfg)
        self.assertTrue(publisher.connect())

        data = AccountData(
            account=Account(account_no=account_no),
            balance=Balance(
                account_no=account_no,
                observed_at="2026-06-18T08:00:00+08:00",
                balance_cny=88.12,
                prepay_balance_cny=12.34,
                arrears_cny=0.56,
            ),
            yearly=YearlyReading(
                account_no=account_no,
                year="2026",
                total_usage_kwh=321.0,
                total_charge_cny=123.45,
            ),
            monthly=[
                MonthlyReading(
                    account_no=account_no,
                    year_month=current_month,
                    total_usage_kwh=56.7,
                    total_charge_cny=23.45,
                )
            ],
            daily=[
                DailyReading(
                    account_no=account_no,
                    date=f"{current_month}-17",
                    total_usage_kwh=6.5,
                    valley_usage_kwh=1.0,
                    flat_usage_kwh=2.0,
                    peak_usage_kwh=3.0,
                    tip_usage_kwh=0.5,
                ),
                DailyReading(
                    account_no=account_no,
                    date=f"{current_month}-18",
                    total_usage_kwh=7.5,
                    valley_usage_kwh=1.5,
                    flat_usage_kwh=2.5,
                    peak_usage_kwh=3.5,
                    tip_usage_kwh=0.0,
                ),
            ],
        )

        self.assertTrue(publisher.publish_account_data(data))
        client = FakeClient.instances[-1]
        all_text = "\n".join(f"{topic} {payload}" for topic, payload, _ in client.published)
        self.assertNotIn(account_no, all_text)
        self.assertIn("*********0123", all_text)

        config_messages = {
            topic: json.loads(payload)
            for topic, payload, retain in client.published
            if topic.endswith("/config")
        }
        state_messages = {
            topic: payload
            for topic, payload, retain in client.published
            if topic.endswith("/state")
        }

        balance_topic = "homeassistant/sensor/sgcc_xxxxxxxxx0123/balance/config"
        self.assertIn(balance_topic, config_messages)
        balance_payload = config_messages[balance_topic]
        self.assertEqual(balance_payload["unique_id"], "sgcc_*********0123_balance")
        self.assertEqual(balance_payload["state_topic"], "sgcc/sgcc_xxxxxxxxx0123/balance/state")
        self.assertEqual(balance_payload["unit_of_measurement"], "CNY")
        self.assertEqual(balance_payload["device_class"], "monetary")
        self.assertEqual(balance_payload["state_class"], "total")
        self.assertEqual(balance_payload["device"]["identifiers"], ["sgcc_*********0123"])
        self.assertEqual(balance_payload["device"]["name"], "国网电费 *********0123")
        self.assertEqual(balance_payload["device"]["manufacturer"], "SGCC bridge")
        self.assertTrue(next(retain for topic, _, retain in client.published if topic == balance_topic))
        self.assertEqual(state_messages["sgcc/sgcc_xxxxxxxxx0123/balance/state"], "88.12")

        month_valley_config = config_messages[
            "homeassistant/sensor/sgcc_xxxxxxxxx0123/month_valley/config"
        ]
        self.assertEqual(month_valley_config["unit_of_measurement"], "kWh")
        self.assertEqual(month_valley_config["device_class"], "energy")
        self.assertEqual(month_valley_config["state_class"], "measurement")
        self.assertEqual(state_messages["sgcc/sgcc_xxxxxxxxx0123/month_valley/state"], "2.5")

        self.assertIn("homeassistant/sensor/sgcc_xxxxxxxxx0123/year_usage/config", config_messages)
        self.assertEqual(
            config_messages["homeassistant/sensor/sgcc_xxxxxxxxx0123/year_usage/config"]["state_class"],
            "total_increasing",
        )


if __name__ == "__main__":
    unittest.main()
