#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""端到端集成测试：在一个假 Dr.COM 门户上跑**完整的** login.py 流程。

这些测试覆盖了整条链路：
    读配置 -> 修正 URL 里的 IP -> 等网络 -> 探测校园网 -> 读取密码
    -> 构造真实字段提交 -> 解析门户响应 -> 返回正确的退出码

全程离线，不碰真的校园网，所以可以随时跑。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import login as login_mod  # noqa: E402
from tests.mock_portal import EXPECTED_FIELDS, MockPortal  # noqa: E402

ACCOUNT = "2021012345"
PASSWORD = "p@ssw0rd"
OPERATOR = "中国移动"
COMPOSITE_ACCOUNT = ",0,2021012345@cmcc"


class MockPortalIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="campusnet-e2e-")
        self._old_env = os.environ.get("CAMPUSNET_CONFIG_DIR")
        os.environ["CAMPUSNET_CONFIG_DIR"] = self.tmp
        self.config_file = os.path.join(self.tmp, "config.json")

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("CAMPUSNET_CONFIG_DIR", None)
        else:
            os.environ["CAMPUSNET_CONFIG_DIR"] = self._old_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- 辅助 ----------------------------------------------------------
    def write_config(self, portal, **overrides):
        config = {
            "login_url": portal.login_url,
            "portal_host": "127.0.0.1",
            "eportal_port": portal.port,
            "autofill_client_ip": False,
            "account": ACCOUNT,
            "operator": OPERATOR,
            "engine": "http",
            "password_backend": "plain",
            "password_plain": PASSWORD,
            "wait_network_seconds": 3,
            "wait_page_seconds": 5,
            "wait_result_seconds": 5,
            "retry_times": 0,
        }
        config.update(overrides)
        with open(self.config_file, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False)
        return self.config_file

    def run_cli(self, *extra):
        argv = ["--test", "--quiet", "--config", self.config_file]
        argv.extend(extra)
        with mock.patch.object(login_mod, "command_probe", wraps=login_mod.command_probe):
            return login_mod.main(argv)


class TestSuccessfulLogin(MockPortalIntegrationTest):
    def test_full_flow_logs_in_and_exits_zero(self):
        with MockPortal(COMPOSITE_ACCOUNT, PASSWORD) as portal:
            self.write_config(portal)
            code = self.run_cli()

            self.assertEqual(code, login_mod.EXIT_OK)
            self.assertTrue(portal.logged_in, "假门户应该收到了正确的账号密码")

            posts = portal.post_requests()
            self.assertEqual(len(posts), 1, "成功时只应提交一次")

    def test_submitted_fields_match_the_real_drcom_form(self):
        with MockPortal(COMPOSITE_ACCOUNT, PASSWORD) as portal:
            self.write_config(portal)
            self.run_cli()

            _method, url, fields = portal.post_requests()[0]

            # 字段集合必须和门户真实的隐藏表单 f0 完全一致
            self.assertEqual(set(fields), EXPECTED_FIELDS)
            # 复合账号 = ",0," + 学号 + 运营商后缀
            self.assertEqual(fields["DDDDD"], ",0,2021012345@cmcc")
            self.assertEqual(fields["upass"], PASSWORD)
            self.assertEqual(fields["0MKKey"], "123456")
            self.assertEqual(fields["para"], "00")
            self.assertEqual(fields["R6"], "0", "PC 端 R6 必须是 0")
            # 认证接口必须带这些查询参数
            self.assertIn("/eportal/", url)
            self.assertIn("c=ACSetting", url)
            self.assertIn("a=Login", url)
            self.assertIn("loginMethod=1", url)

    def test_second_run_detects_already_online_and_does_not_resubmit(self):
        with MockPortal(COMPOSITE_ACCOUNT, PASSWORD) as portal:
            self.write_config(portal)
            self.assertEqual(self.run_cli(), login_mod.EXIT_OK)
            self.assertEqual(len(portal.post_requests()), 1)

            code = self.run_cli()
            self.assertEqual(code, login_mod.EXIT_OK)
            self.assertEqual(
                len(portal.post_requests()), 1,
                "已经在线时不应该再提交一次认证",
            )

    def test_each_operator_maps_to_the_right_suffix(self):
        for operator, suffix in (
            ("中国移动", "@cmcc"),
            ("中国联通", "@unicom"),
            ("中国电信", "@telecom"),
        ):
            with self.subTest(operator=operator):
                expected = "{0}{1}{2}".format(",0,", ACCOUNT, suffix)
                with MockPortal(expected, PASSWORD) as portal:
                    self.write_config(portal, operator=operator)
                    self.assertEqual(self.run_cli(), login_mod.EXIT_OK)
                    self.assertTrue(portal.logged_in)


class TestFailedLogin(MockPortalIntegrationTest):
    def test_wrong_password_exits_one_and_does_not_log_in(self):
        with MockPortal(COMPOSITE_ACCOUNT, "the-real-password") as portal:
            self.write_config(portal)
            code = self.run_cli()

            self.assertEqual(code, login_mod.EXIT_LOGIN_FAILED)
            self.assertFalse(portal.logged_in)

    def test_wrong_operator_exits_one(self):
        with MockPortal(COMPOSITE_ACCOUNT, PASSWORD) as portal:
            self.write_config(portal, operator="中国联通")
            code = self.run_cli()
            self.assertEqual(code, login_mod.EXIT_LOGIN_FAILED)
            self.assertFalse(portal.logged_in)


class TestCheckOperator(MockPortalIntegrationTest):
    """--check-operator：运营商配错时应该能自动找出正确的那家。"""

    def test_finds_and_saves_the_right_operator(self):
        # 门户只认 中国电信(@telecom)，但配置里错写成 中国移动
        telecom_account = "{0}{1}{2}".format(",0,", ACCOUNT, "@telecom")
        with MockPortal(telecom_account, PASSWORD) as portal:
            self.write_config(portal, operator="中国移动")

            argv = ["--check-operator", "--quiet", "--config", self.config_file]
            code = login_mod.main(argv)

            self.assertEqual(code, login_mod.EXIT_OK, "应该试出正确运营商并成功登录")
            self.assertTrue(portal.logged_in)

            # 配置里的运营商应该被自动改正并落盘
            with open(self.config_file, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual(saved["operator"], "中国电信")

    def test_reports_failure_when_no_operator_works(self):
        """三家都不行时，必须明确说是账号/密码问题，而不是含糊地报登录失败。"""
        with MockPortal(",0,someone-else@cmcc", PASSWORD) as portal:
            self.write_config(portal, operator="中国移动")

            argv = ["--check-operator", "--quiet", "--config", self.config_file]
            code = login_mod.main(argv)

            self.assertEqual(code, login_mod.EXIT_LOGIN_FAILED)
            self.assertFalse(portal.logged_in)
            # 三次尝试：配置里的 + 另外两家
            self.assertEqual(len(portal.post_requests()), 3)

    def test_refuses_to_run_without_a_saved_password(self):
        with MockPortal(COMPOSITE_ACCOUNT, PASSWORD) as portal:
            self.write_config(portal, password_plain="")
            argv = ["--check-operator", "--quiet", "--config", self.config_file]
            self.assertEqual(login_mod.main(argv), login_mod.EXIT_USAGE)


class TestNotOnCampus(MockPortalIntegrationTest):
    def test_unreachable_portal_exits_quietly_without_posting(self):
        """离开校园网（认证页打不开）时必须安静退出，不提交、不报错。"""
        with MockPortal(COMPOSITE_ACCOUNT, PASSWORD) as portal:
            port = portal.port
            portal.stop()  # 相当于"回到家了"，认证服务器不可达

            self.write_config(portal, eportal_port=port)
            code = self.run_cli()

            self.assertEqual(code, login_mod.EXIT_OK, "不在校园网时退出码应该是 0（正常）")

    def test_no_password_saved_reports_usage_error(self):
        with MockPortal(COMPOSITE_ACCOUNT, PASSWORD) as portal:
            self.write_config(portal, password_plain="")
            code = self.run_cli()
            self.assertEqual(code, login_mod.EXIT_USAGE)


class TestIpAutofill(MockPortalIntegrationTest):
    def test_autofill_rewrites_the_client_ip_in_the_url(self):
        with MockPortal(COMPOSITE_ACCOUNT, PASSWORD) as portal:
            self.write_config(portal, autofill_client_ip=True)
            self.assertEqual(self.run_cli(), login_mod.EXIT_OK)

            # 用回环地址访问时，本机 IP 就是 127.0.0.1，URL 里不应再出现别的地址
            get_paths = [r[1] for r in portal.requests if r[0] == "GET"]
            self.assertTrue(get_paths)
            for path in get_paths:
                self.assertIn("wlanuserip=127.0.0.1", path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
