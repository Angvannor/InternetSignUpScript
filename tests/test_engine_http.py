#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""engine_http 的单元测试：用假 HTTP 客户端跑完整的登录判定流程。

失败页/成功页内容都是从真实门户抓的（见 tests/test_drcom_portal.py 顶部说明）。
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine_http  # noqa: E402
from config_store import Settings  # noqa: E402

LOGIN_URL = (
    "http://172.16.2.100/a70.htm?wlanuserip=10.53.53.219&wlanacip=null"
    "&wlanacname=null&vlanid=0&ip=10.53.53.219&ssid=null&areaID=null"
    "&mac=00-00-00-00-00-00"
)

LOGIN_PAGE = "<html><!--Dr.COMWebLoginID_0.htm--><input name='upass'></html>"
ONLINE_PAGE = '<html><!--Dr.COMWebLoginID_3.htm--><input name="logout"></html>'
SUCCESS_PAGE = "<html><!--Dr.COMWebLoginID_3.htm--><body>ok</body></html>"
FAILURE_PAGE = (
    "<html><!--Dr.COMWebLoginID_2.htm--><script>"
    "Msg=01;time='1';msga='';</script></html>"
)
WEIRD_PAGE = "<html>完全看不懂的页面</html>"


class ScriptedClient:
    """按脚本顺序返回内容的假客户端。

    注意：这里**不复制**队列，多个客户端（登录 + 复核）共用同一个队列，
    才能按顺序模拟"登录一次、再复核一次"的真实调用序列。
    """

    def __init__(self, get_pages, post_pages, calls):
        self.get_pages = get_pages
        self.post_pages = post_pages
        self.calls = calls

    def _next(self, pages, fallback):
        if pages:
            return pages.pop(0)
        return fallback

    def get(self, url):
        self.calls.append(("GET", url))
        return 200, self._next(self.get_pages, LOGIN_PAGE), url

    def post(self, url, fields):
        self.calls.append(("POST", url, fields))
        return 200, self._next(self.post_pages, FAILURE_PAGE), url


class ClientFactory:
    """engine_http 内部会创建多个客户端（登录一次 + 复核一次），这里统一脚本化。"""

    def __init__(self, get_pages=(), post_pages=()):
        self.get_pages = list(get_pages)
        self.post_pages = list(post_pages)
        self.calls = []

    def __call__(self, timeout=8.0):
        return ScriptedClient(self.get_pages, self.post_pages, self.calls)


def make_settings(**kwargs):
    settings = Settings()
    settings.account = "2021012345"
    settings.operator = "中国移动"
    for key, value in kwargs.items():
        setattr(settings, key, value)
    return settings


class TestLoginWithHttp(unittest.TestCase):
    def _run(self, get_pages=(), post_pages=()):
        factory = ClientFactory(get_pages, post_pages)
        with mock.patch.object(engine_http, "PortalClient", factory):
            result = engine_http.login_with_http(
                make_settings(), "p@ssw0rd", LOGIN_URL, log=lambda _m: None
            )
        return result, factory

    def test_wrong_password_is_reported_as_bad_credentials(self):
        result, _ = self._run(get_pages=[LOGIN_PAGE], post_pages=[FAILURE_PAGE])
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "bad_credentials")
        self.assertIn("账号或密码错误", result.detail)
        self.assertEqual(result.engine, "http")

    def test_success_page_is_reported_as_success(self):
        result, _ = self._run(get_pages=[LOGIN_PAGE], post_pages=[SUCCESS_PAGE])
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "success")

    def test_already_online_short_circuits_without_posting(self):
        result, factory = self._run(get_pages=[ONLINE_PAGE], post_pages=[SUCCESS_PAGE])
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "already_online")
        self.assertFalse([c for c in factory.calls if c[0] == "POST"], "已在线时不应该再提交认证")

    def test_unrecognised_response_is_verified_by_reopening_the_page(self):
        """提交后看不懂返回内容时，会再打开一次登录页复核 -> 在线则判成功。"""
        result, factory = self._run(
            get_pages=[LOGIN_PAGE, ONLINE_PAGE], post_pages=[WEIRD_PAGE]
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "success")
        self.assertEqual(len([c for c in factory.calls if c[0] == "GET"]), 2)

    def test_unrecognised_response_still_showing_login_is_failure(self):
        result, _ = self._run(
            get_pages=[LOGIN_PAGE, LOGIN_PAGE], post_pages=[WEIRD_PAGE]
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "failed")

    def test_payload_uses_real_drcom_field_names(self):
        _, factory = self._run(get_pages=[LOGIN_PAGE], post_pages=[SUCCESS_PAGE])
        post_calls = [c for c in factory.calls if c[0] == "POST"]
        self.assertEqual(len(post_calls), 1)
        _method, url, fields = post_calls[0]

        self.assertIn(":801/eportal/", url)
        self.assertIn("c=ACSetting", url)
        self.assertIn("a=Login", url)
        self.assertEqual(fields["DDDDD"], ",0,2021012345@cmcc")
        self.assertEqual(fields["upass"], "p@ssw0rd")
        self.assertEqual(fields["0MKKey"], "123456")

    def test_portal_unreachable_does_not_raise(self):
        class BoomClient:
            def __init__(self, timeout=8.0):
                pass

            def get(self, url):
                raise OSError("network down")

        with mock.patch.object(engine_http, "PortalClient", BoomClient):
            result = engine_http.login_with_http(
                make_settings(), "p@ssw0rd", LOGIN_URL, log=lambda _m: None
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "portal_unreachable")

    def test_submit_failure_does_not_raise(self):
        class HalfBrokenClient:
            def __init__(self, timeout=8.0):
                pass

            def get(self, url):
                return 200, LOGIN_PAGE, url

            def post(self, url, fields):
                raise OSError("connection reset")

        with mock.patch.object(engine_http, "PortalClient", HalfBrokenClient):
            result = engine_http.login_with_http(
                make_settings(), "p@ssw0rd", LOGIN_URL, log=lambda _m: None
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "submit_failed")

    def test_password_is_never_written_to_the_log(self):
        messages = []
        factory = ClientFactory([LOGIN_PAGE], [FAILURE_PAGE])
        with mock.patch.object(engine_http, "PortalClient", factory):
            engine_http.login_with_http(
                make_settings(), "sup3r-s3cret", LOGIN_URL, log=messages.append
            )
        self.assertNotIn("sup3r-s3cret", "\n".join(messages))


if __name__ == "__main__":
    unittest.main(verbosity=2)
