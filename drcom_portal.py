#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Dr.COM（城市热点）eportal 校园网认证协议 —— 真实协议知识库。

本文件里的每一个常量都来自**华东交通大学实际门户页面**的源码，不是猜测出来的。
证据来源（可用 `python login.py --probe` 在你自己的电脑上复核）：

1. 登录页 HTML：http://172.16.2.100/a70.htm?wlanuserip=...
   页面注释 <!--Dr.COMWebLoginID_0.htm--> 表示这是"第 0 号页面（登录页）"。

2. 页面正文模板（a70.htm 的 body 是空的，界面由它动态拼出来）：
   http://172.16.2.100:801/eportal/extern/test/ip/8/pc.js
   里面能直接读到下面这些真实元素：
       <form name="f3" onsubmit="return ee(3)">
           <input type="text"     name="DDDDD" maxlength="16" placeholder="学号" autocomplete="off">
           <input type="password" name="upass" maxlength="16" placeholder="密码" autocomplete="off">
           <input type="submit"   name="0MKKey" value="登 录">
       </form>
       <select name="ISP_select">
           <option value="-1">选择运营商</option>
           <option value="@cmcc">中国移动</option>
           <option value="@unicom">中国联通</option>
           <option value="@telecom">中国电信</option>
       </select>
       以及一个隐藏表单 f0（真正被 submit 的表单），字段见 HIDDEN_FORM_FIELDS。

3. 登录逻辑：http://172.16.2.100/a41.js
       ee()          -> 校验表单 / 读运营商
       saveLoginForm()-> setISP() 把运营商写进"账号后缀"
       login_portal() -> document.f0.action = "...:801/eportal/?c=ACSetting&a=Login&..."
                         document.f0.DDDDD.value = accountPrefix + 账号 + 运营商后缀
                         document.f0.submit()
   其中 PC 端 accountPrefix = ",0,"（手机端 ",1,"），即最终账号形如：
       ,0,2021xxxxxxxx@cmcc

4. 响应判定（同样来自 a41.js，并用一次假账号实测复核过）：
       成功            -> 响应页面含注释 Dr.COMWebLoginID_3.htm
       账号/密码错误    -> 响应页面含注释 Dr.COMWebLoginID_2.htm，且有 Msg=01; ... msga='';
"""

from __future__ import annotations

import re
from typing import Dict, List, NamedTuple, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# --------------------------------------------------------------------------
# 一、门户地址（默认值，可在 config.json 里覆盖）
# --------------------------------------------------------------------------

#: 你截图里浏览器地址栏的完整地址（换电脑/换 IP 时参数会自动按当前网卡 IP 修正）
DEFAULT_LOGIN_URL = (
    "http://172.16.2.100/a70.htm"
    "?wlanuserip=10.53.53.219"
    "&wlanacip=null"
    "&wlanacname=null"
    "&vlanid=0"
    "&ip=10.53.53.219"
    "&ssid=null"
    "&areaID=null"
    "&mac=00-00-00-00-00-00"
)

#: 认证服务器（a41.js 里 v4serip="172.16.2.100"）
DEFAULT_PORTAL_HOST = "172.16.2.100"

#: eportal 认证接口端口（a41.js 里 :801/eportal/）
EPORTAL_PORT = 801

#: 校园网认证页面的"页面编号"注释，用来判断当前处于哪个状态
PAGE_ID_LOGIN = "Dr.COMWebLoginID_0.htm"    # 登录页
PAGE_ID_INFO = "Dr.COMWebLoginID_2.htm"     # 信息页（登录失败时返回）
PAGE_ID_ONLINE = "Dr.COMWebLoginID_3.htm"   # 成功页 / 已在线页

# --------------------------------------------------------------------------
# 二、真实的 HTML 元素定位信息（直接照抄门户模板，未做任何猜测）
# --------------------------------------------------------------------------

#: 可见的账号输入框（form name="f3" 内，placeholder="学号"）
FIELD_ACCOUNT = "DDDDD"
#: 可见的密码输入框
FIELD_PASSWORD = "upass"
#: 登录按钮（type="submit"，value="登 录"）
FIELD_LOGIN_BUTTON = "0MKKey"
#: 短信验证码登录按钮（本工具不使用，仅记录）
FIELD_LOGIN_BUTTON_SMS = "0MKKey1"
#: 运营商下拉框
FIELD_ISP_SELECT = "ISP_select"
#: 运营商下拉框里"请选择"那项的 value
ISP_UNSELECTED_VALUE = "-1"

#: 真正被提交的隐藏表单名
HIDDEN_FORM_NAME = "f0"

#: 隐藏表单 f0 的全部字段及门户模板里的初始值（照抄 pc.js 模板）
#: 提交时 DDDDD / upass 会被真实账号密码覆盖，R6 由 JS 按 PC/手机改成 0/1。
HIDDEN_FORM_FIELDS = {
    "DDDDD": "",
    "upass": "",
    "R1": "0",
    "R2": "0",
    "R3": "0",
    "R6": "0",
    "para": "00",
    "0MKKey": "123456",
    "buttonClicked": "",
    "redirect_url": "",
    "err_flag": "",
    "username": "",
    "password": "",
    "user": "",
    "cmd": "",
    "Login": "",
}

#: 运营商 -> 账号后缀。取值来自门户自己的 <select name="ISP_select"> 选项 value。
ISP_SUFFIX_BY_NAME: Dict[str, str] = {
    "中国移动": "@cmcc",
    "中国联通": "@unicom",
    "中国电信": "@telecom",
}

#: 运营商下拉框里显示的名字（按此顺序在初始化向导里展示）
ISP_CHOICES: Tuple[str, ...] = ("中国移动", "中国联通", "中国电信")

#: 账号前缀：无感知模块要求 PC 用 ",0,"，手机用 ",1,"（a41.js 第 334 行）
ACCOUNT_PREFIX_PC = ",0,"
ACCOUNT_PREFIX_MOBILE = ",1,"

#: 设备类型：0-其他；1-PC；2-手机；3-平板（a41.js: getTermType）
ITERM_TYPE_PC = 1
ITERM_TYPE_MOBILE = 2

#: 认证方式：1 = Portal 协议（你所在网段 10.52.0.0~10.53.255.255 用的就是它）
LOGIN_METHOD_PORTAL = 1


# --------------------------------------------------------------------------
# 三、URL / 请求载荷构建
# --------------------------------------------------------------------------

def parse_login_url(login_url: str) -> Dict[str, str]:
    """把登录 URL 里的查询参数解析成字典（wlanuserip / mac / session ...）。"""
    query = urlparse(login_url).query
    return dict(parse_qsl(query, keep_blank_values=True))


def portal_address(login_url: str, default_host: str = DEFAULT_PORTAL_HOST) -> Tuple[str, int]:
    """从登录地址里取出 (主机, 端口)，用来判断"网络通没通"。

    需求文档第三条特别提到地址可能是 http://127.0.0.1:xxxx/xxxx 这类形式，
    所以这里不写死 80 端口，也不写死主机名，一切都以配置里的地址为准。

    >>> portal_address("http://172.16.2.100/a70.htm")
    ('172.16.2.100', 80)
    >>> portal_address("http://127.0.0.1:8080/a70.htm")
    ('127.0.0.1', 8080)
    """
    parts = urlparse(login_url)
    host = parts.hostname or default_host
    if parts.port:
        port = parts.port
    else:
        port = 443 if (parts.scheme or "http").lower() == "https" else 80
    return host, port


def autofill_client_ip(login_url: str, local_ip: Optional[str]) -> Tuple[str, bool]:
    """把 URL 里的 wlanuserip / ip 参数改成当前网卡真实 IP。

    门户地址里的 wlanuserip 是你**当时**的 IP。换了 DHCP 租约或换了宿舍网口后
    这个值会过期，认证就会失败，所以每次运行都按当前 IP 修正一次。

    返回 (新的 URL, 是否发生了修改)。local_ip 为 None 时原样返回。
    """
    if not local_ip:
        return login_url, False

    parts = urlparse(login_url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    changed = False
    new_pairs: List[Tuple[str, str]] = []
    for key, value in pairs:
        if key in ("wlanuserip", "ip") and value != local_ip:
            value = local_ip
            changed = True
        new_pairs.append((key, value))

    if not changed:
        return login_url, False

    new_query = urlencode(new_pairs)
    return urlunparse(parts._replace(query=new_query)), True


def isp_suffix(operator: str) -> str:
    """把用户选的运营商名字（或直接写的后缀）转成账号后缀。

    >>> isp_suffix("中国移动")
    '@cmcc'
    >>> isp_suffix("@unicom")
    '@unicom'
    >>> isp_suffix("")
    ''
    """
    operator = (operator or "").strip()
    if not operator:
        return ""
    if operator.startswith("@"):
        return operator
    return ISP_SUFFIX_BY_NAME.get(operator, "")


def build_account(
    account: str,
    operator: str = "",
    account_prefix: str = ACCOUNT_PREFIX_PC,
    isp_suffix_override: Optional[str] = None,
) -> str:
    """拼出提交给门户的"复合账号"。

    门户网页里的做法：accountPrefix + 账号 + 运营商后缀
    例如：账号 2021012345 + 中国移动 -> ",0,2021012345@cmcc"

    isp_suffix_override 用于 --probe 时按实测到的后缀强制覆盖。
    """
    suffix = isp_suffix_override if isp_suffix_override is not None else isp_suffix(operator)
    return "{prefix}{account}{suffix}".format(
        prefix=account_prefix or "", account=(account or "").strip(), suffix=suffix or ""
    )


def build_login_endpoint(
    login_url: str,
    i_term_type: int = ITERM_TYPE_PC,
    login_method: int = LOGIN_METHOD_PORTAL,
    eportal_port: int = EPORTAL_PORT,
) -> str:
    """按 a41.js 的 login_portal() 还原出真正的 POST 目标地址。

    形如：
      http://172.16.2.100:801/eportal/?c=ACSetting&a=Login&protocol=http:&hostname=...
    """
    parts = urlparse(login_url)
    params = parse_login_url(login_url)

    query = {
        "c": "ACSetting",
        "a": "Login",
        "protocol": "{0}:".format(parts.scheme or "http"),
        "hostname": parts.hostname or DEFAULT_PORTAL_HOST,
        "port": str(parts.port or ""),
        "iTermType": str(i_term_type),
        # 下面这些直接沿用登录页 URL 里的原参数，门户自己就是这么取的
        "wlanuserip": params.get("wlanuserip", ""),
        "wlanacip": params.get("wlanacip", ""),
        "wlanacname": params.get("wlanacname", ""),
        "mac": params.get("mac", ""),
        "ip": params.get("ip", params.get("wlanuserip", "")),
        "redirect": params.get("redirect", ""),
        "session": params.get("session", ""),
        "loginMethod": str(login_method),
    }

    base = "{scheme}://{host}:{port}/eportal/".format(
        scheme=parts.scheme or "http",
        host=parts.hostname or DEFAULT_PORTAL_HOST,
        port=eportal_port,
    )
    return base + "?" + urlencode(query)


def build_login_payload(
    account: str,
    password: str,
    operator: str = "",
    account_prefix: str = ACCOUNT_PREFIX_PC,
    i_term_type: int = ITERM_TYPE_PC,
    isp_suffix_override: Optional[str] = None,
) -> Dict[str, str]:
    """构建隐藏表单 f0 的完整提交内容（字段与 pc.js 模板完全一致）。

    R6：门户的 a41.js 里 `document.f0.R6.value = iTermType == 2 ? 1 : 0`
        即手机填 1，PC 填 0。
    """
    payload = dict(HIDDEN_FORM_FIELDS)
    payload["DDDDD"] = build_account(
        account, operator, account_prefix, isp_suffix_override
    )
    payload["upass"] = password
    payload["R6"] = "1" if i_term_type == ITERM_TYPE_MOBILE else "0"
    return payload


# --------------------------------------------------------------------------
# 四、响应内容判定
# --------------------------------------------------------------------------

class LoginOutcome(NamedTuple):
    """一次认证请求的判定结果。"""

    status: str          # success / bad_credentials / failed / unknown
    ok: bool             # 是否登录成功
    code: Optional[str]  # 门户返回的 Msg 代码
    message: str         # 人类可读的说明（含门户原文）

    @property
    def retryable(self) -> bool:
        return self.status == "unknown"


#: Msg 代码 -> 说明。只写已确认的，不编造。
MSG_HINTS = {
    "01": "账号或密码错误",
}


def classify_response(html: str) -> LoginOutcome:
    """判定门户返回的页面代表什么结果。

    已实测确认：
      * 失败页带 <!--Dr.COMWebLoginID_2.htm-->，正文有 Msg=01; 与 msga='';
      * 成功页带 <!--Dr.COMWebLoginID_3.htm-->。
    """
    if not html:
        return LoginOutcome("unknown", False, None, "门户返回了空内容")

    if PAGE_ID_ONLINE in html:
        return LoginOutcome("success", True, "00", "门户返回成功页（Dr.COMWebLoginID_3.htm）")

    code_match = re.search(r"\bMsg\s*=\s*'?(\d+)'?", html)
    msg_match = re.search(r"\bmsga\s*=\s*'([^']*)'", html)
    code = code_match.group(1) if code_match else None
    portal_msg = (msg_match.group(1) if msg_match else "").strip()

    if code == "01" and not portal_msg:
        return LoginOutcome("bad_credentials", False, code, MSG_HINTS["01"])

    if code and code != "00":
        hint = MSG_HINTS.get(code, "门户返回错误码 Msg={0}".format(code))
        detail = "，门户提示：{0}".format(portal_msg) if portal_msg else ""
        return LoginOutcome("failed", False, code, hint + detail)

    if PAGE_ID_INFO in html:
        return LoginOutcome(
            "failed", False, code, "门户返回信息页（Dr.COMWebLoginID_2.htm），但未给出错误码"
        )

    if looks_like_login_page(html):
        return LoginOutcome(
            "login_page", False, code, "门户仍然返回登录页，说明还没有认证成功"
        )

    return LoginOutcome("unknown", False, code, "无法识别的门户返回内容")


def looks_like_login_page(html: str) -> bool:
    """响应内容是不是"需要登录"的登录页。"""
    if not html:
        return False
    if PAGE_ID_ONLINE in html:
        return False
    if PAGE_ID_LOGIN in html:
        return True
    # 兜底：登录页一定带这两个字段
    return "name=\"upass\"" in html or "name='upass'" in html or "name=upass" in html


def looks_like_online_page(html: str) -> bool:
    """响应内容是不是"已经在线"的成功页 / 注销页。"""
    if not html:
        return False
    if PAGE_ID_ONLINE in html:
        return True
    return "name=\"logout\"" in html or "name='logout'" in html


def describe_markers(html: str) -> str:
    """把门户响应里的关键标记压成一行，方便写日志 / 排障。

    例：``页面=Dr.COMWebLoginID_2.htm Msg=01 msga=''``

    这些标记是判断"到底为什么失败"的唯一可靠依据，所以登录失败时一定要记下来。
    """
    page_match = re.search(r"<!--\s*(Dr\.COMWebLoginID_\d+\.htm)\s*-->", html or "")
    code_match = re.search(r"\bMsg\s*=\s*'?(\d+)'?", html or "")
    msg_match = re.search(r"\bmsga\s*=\s*'([^']*)'", html or "")

    parts = ["页面={0}".format(page_match.group(1) if page_match else "未知")]
    parts.append("Msg={0}".format(code_match.group(1) if code_match else "-"))
    if msg_match:
        parts.append("msga='{0}'".format(msg_match.group(1)))
    return " ".join(parts)


# --------------------------------------------------------------------------
# 五、从真实页面里读出运营商下拉框（--probe 用，保证后缀不是我编的）
# --------------------------------------------------------------------------

_SELECT_RE = re.compile(
    r"<select[^>]*name=[\"']?{0}[\"']?[^>]*>(.*?)</select>".format(re.escape(FIELD_ISP_SELECT)),
    re.IGNORECASE | re.DOTALL,
)
_OPTION_RE = re.compile(
    r"<option[^>]*value=[\"']?([^\"'>\s]*)[\"']?[^>]*>(.*?)</option>", re.IGNORECASE | re.DOTALL
)


def parse_isp_options(html: str) -> "Dict[str, str]":
    """从页面 HTML 里解析 <select name="ISP_select"> 的 {显示文字: value}。

    在 --probe 里用来核对"中国移动 -> @cmcc"这类映射是否和学校当前配置一致。
    """
    result: Dict[str, str] = {}
    select_match = _SELECT_RE.search(html or "")
    if not select_match:
        return result
    for value, text in _OPTION_RE.findall(select_match.group(1)):
        text = re.sub(r"<[^>]+>", "", text).strip()
        if text:
            result[text] = value
    return result
