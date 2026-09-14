#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""config_store 的单元测试：配置读写、密码后端、DPAPI 加解密。"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config_store  # noqa: E402
from config_store import PasswordStore, Settings  # noqa: E402


class TempConfigDirMixin:
    """把配置目录指到临时目录，避免污染真实环境。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="campusnet-test-")
        self._old = os.environ.get("CAMPUSNET_CONFIG_DIR")
        os.environ["CAMPUSNET_CONFIG_DIR"] = self.tmp

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CAMPUSNET_CONFIG_DIR", None)
        else:
            os.environ["CAMPUSNET_CONFIG_DIR"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestConfigPaths(TempConfigDirMixin, unittest.TestCase):
    def test_paths_live_inside_config_dir(self):
        self.assertEqual(config_store.config_dir(), __import__("pathlib").Path(self.tmp))
        self.assertTrue(str(config_store.config_path()).startswith(self.tmp))
        self.assertTrue(str(config_store.log_path()).startswith(self.tmp))


class TestLoadSave(TempConfigDirMixin, unittest.TestCase):
    def test_load_returns_defaults_when_missing(self):
        settings = config_store.load_settings()
        self.assertEqual(settings.account, "")
        self.assertEqual(settings.engine, "auto")
        self.assertFalse(config_store.config_exists())

    def test_round_trip(self):
        settings = Settings()
        settings.account = "2021012345"
        settings.operator = "中国移动"
        path = config_store.save_settings(settings)

        self.assertTrue(os.path.exists(path))
        again = config_store.load_settings()
        self.assertEqual(again.account, "2021012345")
        self.assertEqual(again.operator, "中国移动")
        self.assertEqual(again.login_url, settings.login_url)

    def test_saved_file_is_valid_utf8_json(self):
        settings = Settings()
        settings.account = "学号测试"
        path = config_store.save_settings(settings)
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["account"], "学号测试")

    def test_unknown_keys_are_preserved_in_extra(self):
        """配置里出现本版本不认识的键时不能丢，避免升级掉配置。"""
        settings = Settings()
        settings.extra = {"future_option": 42}
        config_store.save_settings(settings)
        again = config_store.load_settings()
        self.assertEqual(again.extra.get("future_option"), 42)

    def test_public_dict_hides_secrets(self):
        settings = Settings()
        settings.password_plain = "my-secret"
        settings.password_dpapi = "Zm9v"
        public = settings.to_public_dict()
        self.assertNotIn("my-secret", json.dumps(public, ensure_ascii=False))
        self.assertEqual(public["password_plain"], "<已保存>")
        self.assertEqual(public["password_dpapi"], "<已保存>")


class TestPasswordStorePlain(TempConfigDirMixin, unittest.TestCase):
    def test_plain_backend_round_trip(self):
        settings = Settings()
        settings.account = "2021012345"
        settings.password_backend = "plain"
        store = PasswordStore(settings)
        self.assertEqual(store.resolve_backend(), "plain")

        self.assertEqual(store.save("p@ssw0rd"), "plain")
        self.assertEqual(store.load(), "p@ssw0rd")

    def test_load_returns_none_when_nothing_saved(self):
        settings = Settings()
        settings.password_backend = "plain"
        store = PasswordStore(settings)
        self.assertIsNone(store.load())

    def test_delete_clears_password(self):
        settings = Settings()
        settings.account = "2021012345"
        settings.password_backend = "plain"
        store = PasswordStore(settings)
        store.save("p@ssw0rd")
        store.delete()
        self.assertIsNone(store.load())
        self.assertEqual(settings.password_plain, "")

    def test_switching_backend_clears_the_other_storage(self):
        """从明文换到 DPAPI 时，必须把明文清掉，不能两份都留。"""
        settings = Settings()
        settings.account = "2021012345"
        settings.password_backend = "plain"
        PasswordStore(settings).save("p@ssw0rd")
        self.assertEqual(settings.password_plain, "p@ssw0rd")

        settings.password_backend = "dpapi"
        PasswordStore(settings).save("p@ssw0rd")
        self.assertEqual(settings.password_plain, "")


@unittest.skipUnless(sys.platform == "win32", "DPAPI 只在 Windows 上可用")
class TestDpapi(unittest.TestCase):
    def test_round_trip_ascii(self):
        cipher = config_store.dpapi_encrypt("hello-world")
        self.assertNotEqual(cipher, "hello-world")
        self.assertEqual(config_store.dpapi_decrypt(cipher), "hello-world")

    def test_round_trip_chinese(self):
        secret = "密码@cmcc-中文测试"
        self.assertEqual(config_store.dpapi_decrypt(config_store.dpapi_encrypt(secret)), secret)

    def test_ciphertext_is_not_plaintext(self):
        cipher = config_store.dpapi_encrypt("p@ssw0rd")
        self.assertNotIn("p@ssw0rd", cipher)

    def test_dpapi_store_round_trip(self):
        settings = Settings()
        settings.account = "2021012345"
        settings.password_backend = "dpapi"
        store = PasswordStore(settings)
        self.assertEqual(store.save("p@ssw0rd"), "dpapi")
        self.assertTrue(settings.password_dpapi)
        self.assertEqual(store.load(), "p@ssw0rd")


if __name__ == "__main__":
    unittest.main(verbosity=2)
