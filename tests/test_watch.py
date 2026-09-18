#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""常驻监视模式（--watch）的单元测试。

这是"息屏/睡眠后掉线要手动重登"的正解，所以行为必须钉死：
  * 在线时什么都不做（不重复提交认证）
  * 掉线（门户要求登录）时自动重新登录
  * 不在校园网时安静等待，不报错
  * 单轮异常不能让整个监视进程死掉
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import login as login_mod  # noqa: E402
import netcheck  # noqa: E402
from config_store import PasswordStore, Settings  # noqa: E402
from engine_http import LoginResult  # noqa: E402

LOGIN_PAGE = "<html><!--Dr.COMWebLoginID_0.htm--><input name='upass'></html>"
ONLINE_PAGE = '<!--Dr.COMWebLoginID_3.htm--><input name="logout">'


def make_settings():
    settings = Settings()
    settings.account = "2021012345"
    settings.operator = "中国移动"
    settings.password_backend = "plain"
    settings.password_plain = "p@ssw0rd"
    settings.autofill_client_ip = False
    return settings


class TestWatchTick(unittest.TestCase):
    def setUp(self):
        self.settings = make_settings()

    def _tick(self, probe_status, detail="x", html=""):
        probe = netcheck.ProbeResult(probe_status, detail, html)
        with mock.patch.object(netcheck, "probe_portal", return_value=probe):
            with mock.patch.object(login_mod.netcheck, "guess_local_ip", return_value="10.0.0.5"):
                return login_mod.watch_tick(self.settings, PasswordStore(self.settings))

    def test_already_online_does_nothing(self):
        """已经在线时绝不能再去提交一次认证。"""
        with mock.patch.object(login_mod, "login_with_http") as fake_login:
            state, _ = self._tick(netcheck.STATUS_ALREADY_ONLINE)
        self.assertEqual(state, login_mod.WATCH_ONLINE)
        self.assertFalse(fake_login.called)

    def test_not_campus_waits_quietly(self):
        with mock.patch.object(login_mod, "login_with_http") as fake_login:
            state, _ = self._tick(netcheck.STATUS_UNREACHABLE, "无法访问")
        self.assertEqual(state, login_mod.WATCH_NOT_CAMPUS)
        self.assertFalse(fake_login.called)

    def test_login_page_triggers_a_login(self):
        """睡醒后掉线 -> 门户要求登录 -> 自动重登。"""
        ok = LoginResult(True, "success", "登录成功")
        with mock.patch.object(login_mod, "login_with_http", return_value=ok) as fake_login:
            state, detail = self._tick(
                netcheck.STATUS_LOGIN_REQUIRED, "要求登录", LOGIN_PAGE
            )
        self.assertEqual(state, login_mod.WATCH_LOGGED_IN)
        self.assertTrue(fake_login.called)
        self.assertIn("登录成功", detail)

    def test_failed_login_is_reported_not_raised(self):
        bad = LoginResult(False, "bad_credentials", "账号或密码错误")
        with mock.patch.object(login_mod, "login_with_http", return_value=bad):
            state, detail = self._tick(netcheck.STATUS_LOGIN_REQUIRED, "要求登录", LOGIN_PAGE)
        self.assertEqual(state, login_mod.WATCH_FAILED)
        self.assertIn("账号或密码错误", detail)

    def test_missing_password_is_reported(self):
        self.settings.password_plain = ""
        with mock.patch.object(login_mod, "login_with_http") as fake_login:
            state, detail = self._tick(netcheck.STATUS_LOGIN_REQUIRED, "要求登录", LOGIN_PAGE)
        self.assertEqual(state, login_mod.WATCH_NO_PASSWORD)
        self.assertFalse(fake_login.called)


class TestRunWatchLoop(unittest.TestCase):
    def test_online_backs_off_and_offline_retries_quickly(self):
        """在线时探测间隔要放大，不在线时要快 —— 这样既不打扰门户又能及时重登。"""
        states = [
            (login_mod.WATCH_LOGGED_IN, "刚重登成功"),
            (login_mod.WATCH_ONLINE, "在线"),
            (login_mod.WATCH_NOT_CAMPUS, "不在校园网"),
            (login_mod.WATCH_FAILED, "失败"),
        ]
        slept = []

        def fake_tick(settings, store, engine_override):
            return states.pop(0)

        code = login_mod.run_watch(
            make_settings(), PasswordStore(make_settings()),
            interval=30.0, online_interval=300.0,
            sleep=slept.append, tick=fake_tick, max_rounds=4,
        )
        self.assertEqual(code, login_mod.EXIT_OK)
        # 掉线/失败 -> 30 秒；在线 -> 300 秒；不在校园网 -> 30 秒
        self.assertEqual(slept, [30.0, 300.0, 30.0])

    def test_a_failing_round_does_not_kill_the_watcher(self):
        """单轮异常必须被吞掉，否则监视进程一崩就再也不会自动重连。"""
        calls = []

        def flaky_tick(settings, store, engine_override):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("模拟网络库炸了")
            return (login_mod.WATCH_ONLINE, "恢复了")

        code = login_mod.run_watch(
            make_settings(), PasswordStore(make_settings()),
            interval=1.0, sleep=lambda _s: None, tick=flaky_tick, max_rounds=2,
        )
        self.assertEqual(code, login_mod.EXIT_OK)
        self.assertEqual(len(calls), 2, "第一轮异常后必须继续第二轮")

    def test_keyboard_interrupt_stops_cleanly(self):
        def fake_tick(settings, store, engine_override):
            return (login_mod.WATCH_ONLINE, "在线")

        def boom(_seconds):
            raise KeyboardInterrupt()

        code = login_mod.run_watch(
            make_settings(), PasswordStore(make_settings()),
            interval=1.0, sleep=boom, tick=fake_tick,
        )
        self.assertEqual(code, login_mod.EXIT_OK)

    def test_interval_can_be_tuned_from_the_command_line(self):
        parser = login_mod.build_parser()
        args = parser.parse_args(["--watch", "--interval", "5"])
        self.assertTrue(args.watch)
        self.assertEqual(args.interval, 5.0)

    def test_watch_flag_is_documented(self):
        self.assertIn("--watch", login_mod.build_parser().format_help())


class TestCrashLogging(unittest.TestCase):
    """pythonw 会吞掉 stderr；开机自启的失败必须留下痕迹。"""

    def setUp(self):
        import shutil
        import tempfile

        self.tmp = tempfile.mkdtemp(prefix="campusnet-crash-")
        self._old = os.environ.get("CAMPUSNET_CONFIG_DIR")
        os.environ["CAMPUSNET_CONFIG_DIR"] = self.tmp

    def tearDown(self):
        import shutil

        if self._old is None:
            os.environ.pop("CAMPUSNET_CONFIG_DIR", None)
        else:
            os.environ["CAMPUSNET_CONFIG_DIR"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_crash_log_creates_a_file(self):
        login_mod._write_crash_log("boom: 测试异常")
        path = os.path.join(self.tmp, "crash.log")
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as handle:
            self.assertIn("boom: 测试异常", handle.read())

    def test_write_crash_log_never_raises(self):
        with mock.patch.object(login_mod, "_crash_log_path", side_effect=OSError("没权限")):
            login_mod._write_crash_log("x")  # 不应该抛

    def test_every_run_leaves_a_heartbeat_in_the_log(self):
        """每次启动都要留一行，否则"脚本今天没跑"就无从判断。

        测试必须离线：把门户探测和联网检测都挡掉。
        """
        login_mod.setup_logging(quiet=True)
        with mock.patch.object(
            netcheck, "probe_portal",
            return_value=netcheck.ProbeResult(netcheck.STATUS_UNREACHABLE, "离线测试"),
        ):
            with mock.patch.object(netcheck, "internet_reachable", return_value=False):
                code = login_mod.main(["--status", "--quiet"])
        self.assertEqual(code, login_mod.EXIT_OK)
        with open(os.path.join(self.tmp, "login.log"), "r", encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("启动：pid=", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
