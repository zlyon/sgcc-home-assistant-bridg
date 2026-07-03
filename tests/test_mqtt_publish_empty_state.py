import unittest
from types import SimpleNamespace

from sgcc_ha_bridge import mqtt_publisher
from sgcc_ha_bridge.config import FetcherConfig
from sgcc_ha_bridge.model import Account, AccountData
from sgcc_ha_bridge.mqtt_publisher import MqttPublisher


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.published = []

    def username_pw_set(self, username, password=None):
        pass

    def connect(self, host, port, keepalive=60):
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


class MqttPublisherEmptyStateTestCase(unittest.TestCase):
    def setUp(self):
        self.original_mqtt = mqtt_publisher.mqtt
        mqtt_publisher.mqtt = SimpleNamespace(
            Client=FakeClient,
            CallbackAPIVersion=SimpleNamespace(VERSION2=object()),
        )

    def tearDown(self):
        mqtt_publisher.mqtt = self.original_mqtt

    def test_discovery_only_without_state_is_not_successful_publish(self):
        publisher = MqttPublisher(FetcherConfig(MQTT_HOST="broker.local"))
        self.assertTrue(publisher.connect())

        ok = publisher.publish_account_data(AccountData(account=Account(account_no="1234567890123")))

        self.assertFalse(ok)
        self.assertEqual(publisher.client.published, [])


if __name__ == "__main__":
    unittest.main()
