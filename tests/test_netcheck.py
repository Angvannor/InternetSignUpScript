#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""netcheck 的单元测试：编解码、等待网络、校园网探测、超时绝不无限等。"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import netcheck  # noqa: E402

LOGIN_PAGE = "<!--Dr.COMWebLoginID_0.htm--><input name='upass'>"
ONLINE_PAGE = '<!--Dr.COMWebLoginID_3.htm--><input name="logout" value="注销">'


class FakeClient:
    """假的 HTTP 客户端：按脚本返回内容。"""

    def __init__(self, page, error=None):
        self.page = page
        self.error = error
        self.calls = []

    def get(self, url):
        self.calls.append(("GET", url))
        if self.error:
            raise self.error
        return 200, self.page, url

    def post(self, url, fields):
        self.calls.append(("POST", url))
        if self.error:
            raise self.error
        return 200, self.page, url


class TestDecode(unittest.TestCase):
    def test_decodes_gb2312_portal_page(self):
        """门户页面声明的是 gb2312，必须能正确解出中文。"""
        raw = "信息页".encode("gb2312")
        self.assertEqual(
            netcheck.PortalClient.decode(raw, "text/html; charset=gb2312"), "信息页"
        )

    def test_decodes_utf8_when_declared(self):
        raw = "信息页".encode("utf-8")
        self.assertEqual(
            netcheck.PortalClient.decode(raw, "text/html; charset=utf-8"), "信息页"
        )

    def test_falls_back_when_charset_unknown(self):
        raw = "信息页".encode("gb18030")
        self.assertEqual(netcheck.PortalClient.decode(raw, "text/html"), "信息页")

    def test_never_raises_on_broken_bytes(self):
        self.assertIsInstance(netcheck.PortalClient.decode(b"\xff\xfe\x00\x81", ""), str)


class TestWaitForNetwork(unittest.TestCase):
    def test_returns_true_immediately_when_reachable(self):
        with mock.patch.object(netcheck, "tcp_reachable", return_value=True):
            self.assertTrue(netcheck.wait_for_network("172.16.2.100", 80, 10))

    def test_returns_false_on_timeout_and_never_hangs(self):
        """超时必须返回 False，而且循环次数有限 —— 绝不无限等待。"""
        ticks = iter([0.0] + [float(i) for i in range(1, 200)])
        sleeps = []

        with mock.patch.object(netcheck, "tcp_reachable", return_value=False):
            result = netcheck.wait_for_network(
                "172.16.2.100", 80, 5,
                interval=1.0,
                sleep=sleeps.append,
                now=lambda: next(ticks),
            )

        self.assertFalse(result)
        self.assertLess(len(sleeps), 20, "超时后不应该继续重试")

    def test_becomes_reachable_after_a_few_tries(self):
        attempts = {"n": 0}

        def flaky(host, port, timeout=2.0):
            attempts["n"] += 1
            return attempts["n"] >= 3

        with mock.patch.object(netcheck, "tcp_reachable", side_effect=flaky):
            self.assertTrue(
                netcheck.wait_for_network("172.16.2.100", 80, 60, sleep=lambda _s: None)
            )
        self.assertEqual(attempts["n"], 3)

    def test_zero_timeout_still_probes_once(self):
        with mock.patch.object(netcheck, "tcp_reachable", return_value=False) as probe:
            self.assertFalse(netcheck.wait_for_network("h", 80, 0))
        self.assertGreaterEqual(probe.call_count, 1)


class TestProbePortal(unittest.TestCase):
    def _probe_with(self, page, error=None):
        client = FakeClient(page, error)
        with mock.patch.object(netcheck, "PortalClient", return_value=client):
            return netcheck.probe_portal("http://172.16.2.100/a70.htm")

    def test_login_page_means_login_required(self):
        result = self._probe_with(LOGIN_PAGE)
        self.assertEqual(result.status, netcheck.STATUS_LOGIN_REQUIRED)
        self.assertTrue(result.html)

    def test_online_page_means_already_online(self):
        result = self._probe_with(ONLINE_PAGE)
        self.assertEqual(result.status, netcheck.STATUS_ALREADY_ONLINE)

    def test_unreachable_means_not_campus(self):
        error = netcheck.urllib.error.URLError("timed out")
        result = self._probe_with("", error)
        self.assertEqual(result.status, netcheck.STATUS_UNREACHABLE)
        self.assertIn("无法访问", result.detail)

    def test_http_error_is_reported_as_error_status(self):
        error = netcheck.urllib.error.HTTPError("u", 502, "bad gateway", None, None)
        result = self._probe_with("", error)
        self.assertEqual(result.status, netcheck.STATUS_ERROR)

    def test_unknown_page_still_treated_as_campus(self):
        """能连上门户但内容认不出来时，宁可交给登录引擎试一次。"""
        result = self._probe_with("<html>???</html>")
        self.assertEqual(result.status, netcheck.STATUS_LOGIN_REQUIRED)


class TestWaitForCampus(unittest.TestCase):
    def test_returns_as_soon_as_campus_is_detected(self):
        pages = [
            netcheck.ProbeResult(netcheck.STATUS_UNREACHABLE, "not yet"),
            netcheck.ProbeResult(netcheck.STATUS_LOGIN_REQUIRED, "yes"),
        ]
        with mock.patch.object(netcheck, "probe_portal", side_effect=pages):
            result = netcheck.wait_for_campus("u", "h", 60, sleep=lambda _s: None)
        self.assertEqual(result.status, netcheck.STATUS_LOGIN_REQUIRED)

    def test_times_out_with_last_result(self):
        ticks = iter([0.0] + [float(i) for i in range(1, 100)])
        with mock.patch.object(
            netcheck, "probe_portal",
            return_value=netcheck.ProbeResult(netcheck.STATUS_UNREACHABLE, "nope"),
        ):
            result = netcheck.wait_for_campus(
                "u", "h", 4, sleep=lambda _s: None, now=lambda: next(ticks)
            )
        self.assertEqual(result.status, netcheck.STATUS_UNREACHABLE)


class TestTcpReachable(unittest.TestCase):
    def test_bad_host_returns_false_without_raising(self):
        self.assertFalse(netcheck.tcp_reachable("203.0.113.1", 9, timeout=0.2))

    def test_guess_local_ip_never_raises(self):
        value = netcheck.guess_local_ip("203.0.113.1")
        self.assertTrue(value is None or isinstance(value, str))


if __name__ == "__main__":
    unittest.main(verbosity=2)
