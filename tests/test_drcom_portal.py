#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""drcom_portal 的单元测试。

测试数据全部是**华东交通大学真实门户返回的内容片段**，不是编造的：
  * 失败页片段  <- 用假账号实际 POST 一次 http://172.16.2.100:801/eportal/ 拿到的
  * 运营商下拉框 <- 从 http://172.16.2.100:801/eportal/extern/test/ip/8/pc.js 抄下来的
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import drcom_portal as portal  # noqa: E402

# --- 真实抓取内容：登录失败时的返回页（截取关键部分） -----------------------
REAL_FAILURE_PAGE = """<!doctype html>
<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=gb2312">
<title>信息页</title>
<!--Dr.COMWebLoginID_2.htm-->
</head>
<body>
<script language="javascript">
Msg=01;time='1234567890';flow='1234567890';fsele=0;fee='1234567890';
ipm="ac100264";ss1="0010f367e3e2";ss4="000000000000";msga='';
</script>
</body>
</html>
"""

# --- 真实抓取内容：登录页（a70.htm） ---------------------------------------
REAL_LOGIN_PAGE = """<!doctype html>
<html><head><title>上网登录窗</title>
<!--Dr.COMWebLoginID_0.htm-->
</head><body></body></html>
"""

# --- 真实抓取内容：运营商下拉框（来自 pc.js 模板） -------------------------
REAL_ISP_SELECT = (
    '<select class="edit_lobo_cell edit_select" name="ISP_select" '
    'style="border-radius: 2px; border: 1px solid rgb(189, 189, 189);">'
    '<option value="-1">选择运营商</option>'
    '<option value="@cmcc">中国移动</option>'
    '<option value="@unicom">中国联通</option>'
    '<option value="@telecom">中国电信</option>'
    "</select>"
)

# --- 真实抓取内容：隐藏表单 f0（真正被提交的表单） -------------------------
REAL_HIDDEN_FORM_FIELDS = [
    "DDDDD", "upass", "R1", "R2", "R3", "R6", "para", "0MKKey",
    "buttonClicked", "redirect_url", "err_flag", "username", "password",
    "user", "cmd", "Login",
]

REAL_LOGIN_URL = (
    "http://172.16.2.100/a70.htm?wlanuserip=10.53.53.219&wlanacip=null"
    "&wlanacname=null&vlanid=0&ip=10.53.53.219&ssid=null&areaID=null"
    "&mac=00-00-00-00-00-00"
)


class TestOperatorSuffix(unittest.TestCase):
    def test_known_operators(self):
        """运营商 -> 后缀的映射必须和门户 select 里的 value 完全一致。"""
        self.assertEqual(portal.isp_suffix("中国移动"), "@cmcc")
        self.assertEqual(portal.isp_suffix("中国联通"), "@unicom")
        self.assertEqual(portal.isp_suffix("中国电信"), "@telecom")

    def test_passthrough_and_empty(self):
        self.assertEqual(portal.isp_suffix("@cmcc"), "@cmcc")
        self.assertEqual(portal.isp_suffix(""), "")
        self.assertEqual(portal.isp_suffix(None), "")
        self.assertEqual(portal.isp_suffix("  中国移动  "), "@cmcc")

    def test_unknown_operator_is_empty(self):
        self.assertEqual(portal.isp_suffix("中国广电"), "")


class TestBuildAccount(unittest.TestCase):
    def test_pc_prefix_matches_portal_behaviour(self):
        """门户 a41.js: accountPrefix + 账号 + 运营商后缀，PC 前缀是 ",0,"。"""
        self.assertEqual(
            portal.build_account("2021012345", "中国移动"),
            ",0,2021012345@cmcc",
        )

    def test_mobile_prefix(self):
        self.assertEqual(
            portal.build_account("2021012345", "中国联通", portal.ACCOUNT_PREFIX_MOBILE),
            ",1,2021012345@unicom",
        )

    def test_no_prefix_no_operator(self):
        self.assertEqual(
            portal.build_account("abc123", "", account_prefix=""), "abc123"
        )

    def test_override_suffix(self):
        self.assertEqual(
            portal.build_account("abc123", "中国移动", isp_suffix_override="@xyz"),
            ",0,abc123@xyz",
        )


class TestHiddenFormFields(unittest.TestCase):
    def test_field_set_matches_real_template(self):
        """隐藏表单 f0 的字段必须和 pc.js 模板完全一致，一个不多一个不少。"""
        self.assertEqual(sorted(portal.HIDDEN_FORM_FIELDS), sorted(REAL_HIDDEN_FORM_FIELDS))

    def test_default_values(self):
        self.assertEqual(portal.HIDDEN_FORM_FIELDS["0MKKey"], "123456")
        self.assertEqual(portal.HIDDEN_FORM_FIELDS["para"], "00")
        self.assertEqual(portal.HIDDEN_FORM_FIELDS["R1"], "0")
        self.assertEqual(portal.HIDDEN_FORM_FIELDS["R2"], "0")
        self.assertEqual(portal.HIDDEN_FORM_FIELDS["R3"], "0")
        self.assertEqual(portal.HIDDEN_FORM_FIELDS["R6"], "0")


class TestBuildPayload(unittest.TestCase):
    def test_payload_contents(self):
        payload = portal.build_login_payload("2021012345", "s3cret", "中国移动")
        self.assertEqual(payload["DDDDD"], ",0,2021012345@cmcc")
        self.assertEqual(payload["upass"], "s3cret")
        self.assertEqual(payload["0MKKey"], "123456")
        self.assertEqual(payload["para"], "00")
        self.assertEqual(payload["R6"], "0", "PC 端 R6 应为 0")
        self.assertEqual(len(payload), len(REAL_HIDDEN_FORM_FIELDS))

    def test_mobile_sets_r6_to_one(self):
        payload = portal.build_login_payload(
            "2021012345", "s3cret", "中国移动", i_term_type=portal.ITERM_TYPE_MOBILE
        )
        self.assertEqual(payload["R6"], "1")

    def test_payload_does_not_alias_template(self):
        payload = portal.build_login_payload("a", "b", "中国移动")
        payload["DDDDD"] = "changed"
        self.assertEqual(portal.HIDDEN_FORM_FIELDS["DDDDD"], "")


class TestBuildLoginEndpoint(unittest.TestCase):
    def test_endpoint_shape(self):
        url = portal.build_login_endpoint(REAL_LOGIN_URL)
        self.assertTrue(url.startswith("http://172.16.2.100:801/eportal/?"))
        self.assertIn("c=ACSetting", url)
        self.assertIn("a=Login", url)
        self.assertIn("loginMethod=1", url)
        self.assertIn("iTermType=1", url)
        self.assertIn("wlanuserip=10.53.53.219", url)
        self.assertIn("hostname=172.16.2.100", url)

    def test_endpoint_uses_801_not_page_port(self):
        """认证接口在 801 端口，不是页面的 80 端口。"""
        self.assertIn(":801/", portal.build_login_endpoint(REAL_LOGIN_URL))


class TestAutofillClientIp(unittest.TestCase):
    def test_replaces_both_ip_params(self):
        new_url, changed = portal.autofill_client_ip(REAL_LOGIN_URL, "10.53.99.42")
        self.assertTrue(changed)
        self.assertIn("wlanuserip=10.53.99.42", new_url)
        self.assertIn("ip=10.53.99.42", new_url)
        self.assertNotIn("10.53.53.219", new_url)

    def test_no_change_when_same(self):
        new_url, changed = portal.autofill_client_ip(REAL_LOGIN_URL, "10.53.53.219")
        self.assertFalse(changed)
        self.assertEqual(new_url, REAL_LOGIN_URL)

    def test_none_ip_is_noop(self):
        new_url, changed = portal.autofill_client_ip(REAL_LOGIN_URL, None)
        self.assertFalse(changed)
        self.assertEqual(new_url, REAL_LOGIN_URL)


class TestParseLoginUrl(unittest.TestCase):
    def test_parses_wlan_params(self):
        params = portal.parse_login_url(REAL_LOGIN_URL)
        self.assertEqual(params["wlanuserip"], "10.53.53.219")
        self.assertEqual(params["mac"], "00-00-00-00-00-00")
        self.assertEqual(params["vlanid"], "0")


class TestClassifyResponse(unittest.TestCase):
    def test_real_failure_page_is_bad_credentials(self):
        """实测过的失败页：Msg=01 + msga='' -> 账号或密码错误。"""
        outcome = portal.classify_response(REAL_FAILURE_PAGE)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "bad_credentials")
        self.assertEqual(outcome.code, "01")
        self.assertIn("账号或密码错误", outcome.message)

    def test_success_page(self):
        html = "<html><!--Dr.COMWebLoginID_3.htm--><body>ok</body></html>"
        outcome = portal.classify_response(html)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.status, "success")

    def test_other_msg_code_is_failure_not_bad_credentials(self):
        html = "<!--Dr.COMWebLoginID_2.htm--><script>Msg=07;msga='其他错误';</script>"
        outcome = portal.classify_response(html)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.code, "07")
        self.assertIn("其他错误", outcome.message)

    def test_empty_response_is_unknown(self):
        outcome = portal.classify_response("")
        self.assertEqual(outcome.status, "unknown")
        self.assertTrue(outcome.retryable)

    def test_unrecognised_response_is_unknown(self):
        outcome = portal.classify_response("<html>hello</html>")
        self.assertEqual(outcome.status, "unknown")

    def test_login_page_is_reported_as_login_page_not_unknown(self):
        """提交后门户又回一个登录页 -> 明确说"未认证成功"，而不是含糊的 unknown。"""
        outcome = portal.classify_response(REAL_LOGIN_PAGE)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "login_page")
        self.assertFalse(outcome.retryable)


class TestPageDetection(unittest.TestCase):
    def test_login_page(self):
        self.assertTrue(portal.looks_like_login_page(REAL_LOGIN_PAGE))
        self.assertFalse(portal.looks_like_online_page(REAL_LOGIN_PAGE))

    def test_login_page_by_field_fallback(self):
        """没有页面编号注释时，靠 upass 字段也能认出来。"""
        self.assertTrue(portal.looks_like_login_page('<input name="upass">'))

    def test_online_page(self):
        html = '<html><!--Dr.COMWebLoginID_3.htm--><input name="logout" value="注销"></html>'
        self.assertTrue(portal.looks_like_online_page(html))
        self.assertFalse(portal.looks_like_login_page(html))

    def test_empty_html(self):
        self.assertFalse(portal.looks_like_login_page(""))
        self.assertFalse(portal.looks_like_online_page(""))


class TestParseIspOptions(unittest.TestCase):
    def test_parses_real_select(self):
        options = portal.parse_isp_options(REAL_ISP_SELECT)
        self.assertEqual(
            options,
            {
                "选择运营商": "-1",
                "中国移动": "@cmcc",
                "中国联通": "@unicom",
                "中国电信": "@telecom",
            },
        )

    def test_matches_our_constant_mapping(self):
        """页面上的真实映射必须和代码里的常量一致（不一致就说明学校改配置了）。"""
        options = portal.parse_isp_options(REAL_ISP_SELECT)
        for name, suffix in portal.ISP_SUFFIX_BY_NAME.items():
            self.assertIn(name, options)
            self.assertEqual(options[name], suffix)

    def test_no_select_returns_empty(self):
        self.assertEqual(portal.parse_isp_options("<html>nothing</html>"), {})

    def test_empty_input(self):
        self.assertEqual(portal.parse_isp_options(""), {})

    def test_unselected_value_constant(self):
        options = portal.parse_isp_options(REAL_ISP_SELECT)
        self.assertEqual(options["选择运营商"], portal.ISP_UNSELECTED_VALUE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
