#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""login.py 命令行辅助逻辑的单元测试（不联网、不弹浏览器）。"""

import logging
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config_store  # noqa: E402
import login as login_mod  # noqa: E402
from config_store import PasswordStore, Settings  # noqa: E402


class CliTestBase(unittest.TestCase):
    def setUp(self):
        logging.getLogger("campusnet").addHandler(logging.NullHandler())
        self.tmp = tempfile.mkdtemp(prefix="campusnet-cli-")
        self._old = os.environ.get("CAMPUSNET_CONFIG_DIR")
        os.environ["CAMPUSNET_CONFIG_DIR"] = self.tmp
        for key in ("CAMPUSNET_ACCOUNT", "CAMPUSNET_OPERATOR", "CAMPUSNET_PASSWORD"):
            os.environ.pop(key, None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CAMPUSNET_CONFIG_DIR", None)
        else:
            os.environ["CAMPUSNET_CONFIG_DIR"] = self._old
        for key in ("CAMPUSNET_ACCOUNT", "CAMPUSNET_OPERATOR", "CAMPUSNET_PASSWORD"):
            os.environ.pop(key, None)
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestOperatorChoice(CliTestBase):
    def test_number_choices(self):
        settings = Settings()
        self.assertEqual(login_mod._parse_operator_choice("1", settings), "中国移动")
        self.assertEqual(login_mod._parse_operator_choice("2", settings), "中国联通")
        self.assertEqual(login_mod._parse_operator_choice("3", settings), "中国电信")

    def test_name_and_suffix_choices(self):
        settings = Settings()
        self.assertEqual(login_mod._parse_operator_choice("中国移动", settings), "中国移动")
        self.assertEqual(login_mod._parse_operator_choice("@cmcc", settings), "@cmcc")

    def test_short_name_is_matched(self):
        settings = Settings()
        self.assertEqual(login_mod._parse_operator_choice("移动", settings), "中国移动")
        self.assertEqual(login_mod._parse_operator_choice("联通", settings), "中国联通")

    def test_invalid_choice_returns_empty(self):
        settings = Settings()
        self.assertEqual(login_mod._parse_operator_choice("99", settings), "")
        self.assertEqual(login_mod._parse_operator_choice("不知道", settings), "")


class TestResolveEngine(CliTestBase):
    def test_cli_override_wins(self):
        settings = Settings()
        settings.engine = "selenium"
        self.assertEqual(login_mod._resolve_engine(settings, "http"), "http")

    def test_falls_back_to_settings(self):
        settings = Settings()
        settings.engine = "http"
        self.assertEqual(login_mod._resolve_engine(settings, None), "http")

    def test_unknown_engine_becomes_auto(self):
        settings = Settings()
        settings.engine = "nonsense"
        self.assertEqual(login_mod._resolve_engine(settings, None), "auto")


class TestParser(CliTestBase):
    def test_all_documented_flags_exist(self):
        parser = login_mod.build_parser()
        for flag in ("--setup", "--test", "--probe", "--status", "--forget",
                     "--engine", "--config", "--wait", "--headless", "--verbose", "--quiet"):
            self.assertIn(flag, parser.format_help(), "{0} 应该在帮助里".format(flag))

    def test_engine_only_accepts_known_values(self):
        parser = login_mod.build_parser()
        self.assertEqual(parser.parse_args(["--engine", "http"]).engine, "http")
        with self.assertRaises(SystemExit):
            parser.parse_args(["--engine", "nope"])

    def test_quiet_and_non_interactive_never_prompt(self):
        """开机自启时没有配置也不能停下来等输入，必须直接退出。"""
        settings = Settings()
        with mock.patch.object(config_store, "load_settings", return_value=settings):
            with mock.patch.object(login_mod, "setup_logging"):
                with mock.patch("builtins.input", side_effect=AssertionError("不应该提问")):
                    code = login_mod.main(["--quiet", "--non-interactive"])
        self.assertEqual(code, login_mod.EXIT_USAGE)

    def test_start_bat_passes_the_unattended_flags(self):
        """start.bat 必须带 --quiet --non-interactive，否则开机可能挂住。"""
        import io
        with io.open(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "start.bat"),
            "r", encoding="ascii",
        ) as handle:
            text = handle.read()
        self.assertIn("--quiet", text)
        self.assertIn("--non-interactive", text)

    def test_batch_files_are_ascii_only(self):
        """cmd.exe 按 OEM 代码页读 .bat，含非 ASCII 字符会被错误解码并可能报错。"""
        import glob
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bat_files = sorted(glob.glob(os.path.join(root, "*.bat")))
        self.assertTrue(bat_files, "应该找得到 .bat 文件")
        for path in bat_files:
            with open(path, "rb") as handle:
                raw = handle.read()
            non_ascii = [b for b in raw if b > 0x7F]
            self.assertEqual(
                non_ascii, [], "{0} 必须是纯 ASCII".format(os.path.basename(path))
            )


class TestRunSetup(CliTestBase):
    def test_non_interactive_setup_from_env_saves_everything(self):
        os.environ["CAMPUSNET_ACCOUNT"] = "2021012345"
        os.environ["CAMPUSNET_OPERATOR"] = "中国移动"
        os.environ["CAMPUSNET_PASSWORD"] = "p@ssw0rd"

        settings = Settings()
        settings.password_backend = "plain"

        with mock.patch.object(login_mod, "run_login", return_value=login_mod.EXIT_OK) as fake:
            code = login_mod.run_setup(settings, PasswordStore(settings), interactive=False)

        self.assertEqual(code, login_mod.EXIT_OK)
        self.assertTrue(fake.called, "初始化后应该立刻试登录一次")
        self.assertEqual(settings.account, "2021012345")
        self.assertEqual(settings.operator, "中国移动")
        self.assertEqual(PasswordStore(settings).load(), "p@ssw0rd")
        self.assertTrue(config_store.config_exists())

    def test_engine_flag_is_forwarded_to_the_first_login(self):
        """--setup --engine http 时，第一次试登录也必须用 http 引擎。"""
        os.environ["CAMPUSNET_ACCOUNT"] = "2021012345"
        os.environ["CAMPUSNET_OPERATOR"] = "中国移动"
        os.environ["CAMPUSNET_PASSWORD"] = "p@ssw0rd"

        settings = Settings()
        settings.password_backend = "plain"
        with mock.patch.object(login_mod, "run_login", return_value=0) as fake:
            login_mod.run_setup(
                settings, PasswordStore(settings), interactive=False, engine_override="http"
            )
        self.assertEqual(fake.call_args.kwargs.get("engine_override"), "http")

    def test_missing_password_does_not_save_anything(self):
        os.environ["CAMPUSNET_ACCOUNT"] = "2021012345"
        os.environ["CAMPUSNET_OPERATOR"] = "中国移动"

        settings = Settings()
        settings.password_backend = "plain"
        with mock.patch.object(login_mod, "run_login", return_value=0):
            code = login_mod.run_setup(settings, PasswordStore(settings), interactive=False)

        self.assertEqual(code, login_mod.EXIT_USAGE)
        self.assertFalse(config_store.config_exists())

    def test_missing_operator_does_not_save_anything(self):
        os.environ["CAMPUSNET_ACCOUNT"] = "2021012345"
        os.environ["CAMPUSNET_PASSWORD"] = "p@ssw0rd"

        settings = Settings()
        settings.password_backend = "plain"
        with mock.patch.object(login_mod, "run_login", return_value=0):
            code = login_mod.run_setup(settings, PasswordStore(settings), interactive=False)

        self.assertEqual(code, login_mod.EXIT_USAGE)
        self.assertFalse(config_store.config_exists())


class TestForgetAndStatus(CliTestBase):
    def test_forget_clears_password_but_keeps_account(self):
        settings = Settings()
        settings.account = "2021012345"
        settings.password_backend = "plain"
        store = PasswordStore(settings)
        store.save("p@ssw0rd")
        config_store.save_settings(settings)

        self.assertEqual(login_mod.command_forget(settings, store), login_mod.EXIT_OK)

        reloaded = config_store.load_settings()
        self.assertEqual(reloaded.account, "2021012345")
        self.assertEqual(reloaded.password_plain, "")


class TestQuietMode(CliTestBase):
    def test_quiet_adds_no_console_handler(self):
        login_mod.setup_logging(verbose=False, quiet=True)
        handlers = logging.getLogger("campusnet").handlers
        self.assertTrue(all(not isinstance(h, logging.StreamHandler) or
                            isinstance(h, logging.handlers.RotatingFileHandler)
                            for h in handlers))

    def test_log_file_is_created_next_to_config(self):
        import logging.handlers as handlers

        login_mod.setup_logging()
        names = [type(h).__name__ for h in logging.getLogger("campusnet").handlers]
        self.assertIn("RotatingFileHandler", names)
        self.assertTrue(any(isinstance(h, handlers.RotatingFileHandler)
                            for h in logging.getLogger("campusnet").handlers))


if __name__ == "__main__":
    unittest.main(verbosity=2)
