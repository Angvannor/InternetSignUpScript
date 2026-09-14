#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一个假的 Dr.COM 门户，用来在**完全离线**的情况下做端到端集成测试。

它模拟真实门户的两个端点：
    GET  /a70.htm    -> 登录页（带 <!--Dr.COMWebLoginID_0.htm-->）
    POST /eportal/   -> 认证；账号密码对就返回成功页，否则返回 Msg=01 失败页

真实门户返回的页面长什么样，见 tests/test_drcom_portal.py 顶部的抓取说明。
"""

from __future__ import annotations

import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOGIN_PAGE = (
    "<!doctype html><html><head><title>上网登录窗</title>\n"
    "<!--Dr.COMWebLoginID_0.htm-->\n"
    "<script>v46ip='127.0.0.1';ss5=\"127.0.0.1\";ss6=\"172.16.2.100\";</script>\n"
    "</head><body></body></html>"
)

SUCCESS_PAGE = (
    "<!doctype html><html><head><title>信息页</title>\n"
    "<!--Dr.COMWebLoginID_3.htm-->\n"
    "</head><body><input type='button' name='logout' value='注 销'></body></html>"
)

FAILURE_PAGE = (
    "<!doctype html><html><head><title>信息页</title>\n"
    "<!--Dr.COMWebLoginID_2.htm-->\n"
    "</head><body><script>\n"
    "Msg=01;time='1234567890';flow='1';ipm=\"ac100264\";ss4=\"000000000000\";msga='';\n"
    "</script></body></html>"
)

#: 门户真正期望收到的表单字段（照抄 pc.js 里的隐藏表单 f0）
EXPECTED_FIELDS = {
    "DDDDD", "upass", "R1", "R2", "R3", "R6", "para", "0MKKey",
    "buttonClicked", "redirect_url", "err_flag", "username", "password",
    "user", "cmd", "Login",
}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # -- 工具 ----------------------------------------------------------
    def _send(self, body: str, code: int = 200) -> None:
        raw = body.encode("gb2312", errors="replace")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=gb2312")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args) -> None:  # 别把测试输出搞乱
        pass

    # -- 路由 ----------------------------------------------------------
    def do_GET(self) -> None:
        server = self.server
        server.requests.append(("GET", self.path, None))
        if self.path.startswith("/a70.htm"):
            if server.logged_in:
                self._send(SUCCESS_PAGE)
            elif getattr(server, "ac_client_ip", None):
                # 模拟"认证服务器看到的 IP 与本机网卡 IP 不同"（宿舍路由器 NAT）
                page = LOGIN_PAGE.replace(
                    "v46ip='127.0.0.1';ss5=\"127.0.0.1\";",
                    "v46ip='{0}';ss5=\"{0}\";".format(server.ac_client_ip),
                )
                self._send(page)
            else:
                self._send(LOGIN_PAGE)
        else:
            self._send("<html>not found</html>", code=404)

    def do_POST(self) -> None:
        server = self.server
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        fields = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))
        server.requests.append(("POST", self.path, fields))

        if not self.path.startswith("/eportal/"):
            self._send("<html>not found</html>", code=404)
            return

        account_ok = fields.get("DDDDD") == server.expected_account
        password_ok = fields.get("upass") == server.expected_password
        if account_ok and password_ok:
            server.logged_in = True
            self._send(SUCCESS_PAGE)
        else:
            self._send(FAILURE_PAGE)


class MockPortal:
    """把假门户跑在 127.0.0.1 的一个随机空闲端口上。

    用法：
        with MockPortal(account=",0,2021012345@cmcc", password="pw") as portal:
            portal.login_url   # http://127.0.0.1:<port>/a70.htm?...
            portal.port
            portal.logged_in   # 端口收到正确凭据后变成 True
    """

    def __init__(self, account: str, password: str, ac_client_ip: str = ""):
        self.expected_account = account
        self.expected_password = password
        #: 模拟"认证服务器看到的客户端 IP"（留空表示与 127.0.0.1 相同）
        self.ac_client_ip = ac_client_ip
        self.requests = []
        self.server = None

    def start(self) -> "MockPortal":
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        # handler 通过 self.server 读状态
        self.server.logged_in = False
        self.server.requests = self.requests
        self.server.expected_account = self.expected_account
        self.server.expected_password = self.expected_password
        self.server.ac_client_ip = self.ac_client_ip
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    @property
    def logged_in(self) -> bool:
        """假门户是否收到过正确的账号密码。"""
        return bool(self.server and self.server.logged_in)

    @property
    def login_url(self) -> str:
        return (
            "http://127.0.0.1:{0}/a70.htm"
            "?wlanuserip=127.0.0.1&wlanacip=null&wlanacname=null&vlanid=0"
            "&ip=127.0.0.1&ssid=null&areaID=null&mac=00-00-00-00-00-00".format(self.port)
        )

    def post_requests(self):
        return [r for r in self.requests if r[0] == "POST"]

    def __enter__(self) -> "MockPortal":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
