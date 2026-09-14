#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""网络等待 + 校园网环境探测（对应需求文档第九、十、十三条）。

核心判断顺序：

    Windows 登录
        -> 等网络（最多 wait_network_seconds 秒，绝不无限等）
        -> 访问校园网认证地址
             可达 且 是登录页  -> 是校园网，需要登录
             可达 且 是在线页  -> 已经在线，什么都不用做
             不可达            -> 不是校园网（例如在家），安静退出
"""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from typing import Callable, Dict, NamedTuple, Optional, Tuple

from drcom_portal import looks_like_login_page, looks_like_online_page

#: 探测结果状态
STATUS_LOGIN_REQUIRED = "login_required"   # 在校园网里，且需要登录
STATUS_ALREADY_ONLINE = "already_online"   # 在校园网里，但已经在线
STATUS_UNREACHABLE = "unreachable"         # 访问不到认证地址（多半不在校园网）
STATUS_ERROR = "error"                     # 能连但出错了

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
)


class ProbeResult(NamedTuple):
    status: str
    detail: str
    html: str = ""
    final_url: str = ""


# --------------------------------------------------------------------------
# HTTP 会话
# --------------------------------------------------------------------------

class PortalClient:
    """一个极简的、**不走系统代理**的 HTTP 客户端。

    为什么不走代理：
      校园网认证服务器是内网地址（172.16.2.100）。如果电脑上开着
      Clash / v2ray 之类的系统代理，urllib 默认会把请求丢给代理，
      于是永远访问不到门户。这里直接禁用代理。
    """

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self.cookies = CookieJar()
        # ProxyHandler({}) == 不使用任何代理
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.cookies),
        )
        self.opener.addheaders = [
            ("User-Agent", USER_AGENT),
            ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
            ("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8"),
        ]

    # -- 编解码 --------------------------------------------------------
    @staticmethod
    def decode(raw: bytes, content_type: str = "") -> str:
        """门户页面是 gb2312 编码的，这里按优先级尝试解码。"""
        candidates = []
        if "charset=" in content_type.lower():
            candidates.append(content_type.lower().split("charset=")[-1].split(";")[0].strip())
        candidates += ["gb18030", "utf-8", "latin-1"]
        for encoding in candidates:
            try:
                return raw.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
        return raw.decode("utf-8", errors="replace")

    def request(
        self, url: str, data: Optional[Dict[str, str]] = None, method: Optional[str] = None
    ) -> Tuple[int, str, str]:
        """发一个请求，返回 (状态码, 文本, 最终URL)。"""
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            method = method or "POST"
        request = urllib.request.Request(url, data=body, method=method or "GET")

        response = self.opener.open(request, timeout=self.timeout)
        try:
            raw = response.read()
            text = self.decode(raw, response.headers.get("Content-Type", ""))
            return response.getcode(), text, response.geturl()
        finally:
            response.close()

    def get(self, url: str) -> Tuple[int, str, str]:
        return self.request(url, method="GET")

    def post(self, url: str, fields: Dict[str, str]) -> Tuple[int, str, str]:
        return self.request(url, data=fields, method="POST")


# --------------------------------------------------------------------------
# 网络等待
# --------------------------------------------------------------------------

def tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    """TCP 能不能连上（判断"网络是否已经通"最快的方法）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def guess_local_ip(remote_host: str) -> Optional[str]:
    """猜出"用来访问 remote_host 的本机 IP"。

    用一个不真正发包的 UDP connect 让系统帮我们选路由，比读注册表可靠。
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(1.0)
            sock.connect((remote_host, 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def wait_for_network(
    host: str,
    port: int,
    timeout_seconds: int,
    interval: float = 1.5,
    log: Optional[Callable[[str], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> bool:
    """等待网络连上 host:port，最多 timeout_seconds 秒。

    开机时网卡往往是"已启用但还没拿到 IP"，所以要有个等待阶段，
    但**绝不允许无限等待** —— 超时就返回 False，由调用方安静退出。
    """
    deadline = now() + max(0, timeout_seconds)
    attempt = 0
    while True:
        attempt += 1
        if tcp_reachable(host, port, timeout=1.5):
            if log:
                log("网络已连通：{0}:{1}（第 {2} 次探测）".format(host, port, attempt))
            return True
        if now() >= deadline:
            if log:
                log("等待网络超时（{0} 秒），放弃。".format(timeout_seconds))
            return False
        sleep(interval)


# --------------------------------------------------------------------------
# 校园网探测
# --------------------------------------------------------------------------

def probe_portal(login_url: str, timeout: float = 8.0) -> ProbeResult:
    """访问校园网认证地址，判断当前环境。"""
    client = PortalClient(timeout=timeout)
    try:
        _, text, final_url = client.get(login_url)
    except urllib.error.HTTPError as error:
        return ProbeResult(STATUS_ERROR, "认证页面返回 HTTP {0}".format(error.code))
    except urllib.error.URLError as error:
        return ProbeResult(
            STATUS_UNREACHABLE, "无法访问认证页面（{0}）".format(error.reason)
        )
    except Exception as error:  # 超时、编码异常等
        return ProbeResult(STATUS_UNREACHABLE, "无法访问认证页面（{0}）".format(error))

    if looks_like_online_page(text):
        return ProbeResult(STATUS_ALREADY_ONLINE, "认证页面显示当前设备已经在线", text, final_url)

    if looks_like_login_page(text):
        return ProbeResult(STATUS_LOGIN_REQUIRED, "认证页面要求输入账号密码", text, final_url)

    # 认不出来：能连上说明大概率在校园网里，交给登录引擎去试
    return ProbeResult(
        STATUS_LOGIN_REQUIRED,
        "认证页面内容无法识别，仍按需要登录处理",
        text,
        final_url,
    )


def wait_for_campus(
    login_url: str,
    portal_host: str,
    timeout_seconds: int,
    interval: float = 2.0,
    log: Optional[Callable[[str], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> ProbeResult:
    """在超时时间内反复探测，直到确认"在校园网里"或超时。

    和 wait_for_network 的区别：这里要的是"认证页能打开"，
    因为有些校园网在拿不到 IP 前 TCP 连得上但 HTTP 打不开。
    """
    deadline = now() + max(0, timeout_seconds)
    last = ProbeResult(STATUS_UNREACHABLE, "还未开始探测")

    while True:
        last = probe_portal(login_url)
        if last.status in (STATUS_LOGIN_REQUIRED, STATUS_ALREADY_ONLINE):
            if log:
                log("已在校园网环境：{0}".format(last.detail))
            return last
        if now() >= deadline:
            if log:
                log("等待校园网超时（{0} 秒）：{1}".format(timeout_seconds, last.detail))
            return last
        sleep(interval)


def internet_reachable(timeout: float = 4.0) -> bool:
    """能不能真的上外网（用于确认"是不是已经在线了"）。

    注意：**不能只看 HTTP 200**。被劫持到认证页时，门户会对任意
    http 请求都回一个 200，于是 200 并不代表能上网。所以这里还要
    校验响应正文，只有拿到微软那段固定文本才算真的通网。
    """
    try:
        with urllib.request.urlopen(
            urllib.request.Request(
                "http://www.msftconnecttest.com/connecttest.txt",
                headers={"User-Agent": USER_AGENT},
            ),
            timeout=timeout,
        ) as response:
            if response.getcode() != 200:
                return False
            body = response.read(200).decode("utf-8", errors="ignore").strip()
            return body == "Microsoft Connect Test"
    except Exception:
        return False
