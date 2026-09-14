#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""纯标准库（零第三方依赖）登录引擎。

它做的事和浏览器点"登 录"完全一样，只是不用开浏览器：

    1. GET  登录页 a70.htm          -> 拿到 cookie，顺便确认门户可达
    2. POST http://172.16.2.100:801/eportal/?c=ACSetting&a=Login&...
       表单内容 = 隐藏表单 f0 的全部字段（DDDDD / upass / R1 / R2 / R3 / R6 ...）
    3. 读返回页面，判断成功还是失败

为什么要有这个引擎：
  校园网没登录时上不了外网 -> pip install 不了 selenium。
  所以"第一次在这台电脑上跑"必须能零依赖完成登录，
  这个引擎就是那个兜底。它也比起浏览器快得多、开机时不会弹窗口。
"""

from __future__ import annotations

from typing import Callable, List, Optional

from drcom_portal import (
    ITERM_TYPE_PC,
    LOGIN_METHOD_PORTAL,
    PAGE_ID_ONLINE,
    LoginOutcome,
    build_login_endpoint,
    build_login_payload,
    classify_response,
    looks_like_login_page,
    looks_like_online_page,
)
from config_store import Settings
from netcheck import PortalClient


class LoginResult:
    """登录结果（两个引擎统一返回这个）。"""

    def __init__(self, ok: bool, status: str, detail: str, engine: str = "http"):
        self.ok = ok
        self.status = status
        self.detail = detail
        self.engine = engine

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "LoginResult(ok={0!r}, status={1!r}, detail={2!r}, engine={3!r})".format(
            self.ok, self.status, self.detail, self.engine
        )


def _noop(message: str) -> None:
    pass


def login_with_http(
    settings: Settings,
    password: str,
    login_url: str,
    log: Optional[Callable[[str], None]] = None,
    verify: bool = True,
) -> LoginResult:
    """用标准库直接提交门户的认证表单。

    :param login_url: 已经按当前 IP 修正过的登录地址
    :param verify:    提交后是否再访问一次登录页复核登录状态
    """
    log = log or _noop
    client = PortalClient(timeout=max(8.0, float(settings.wait_page_seconds)))

    # ---- 1. 先打开登录页（拿 cookie，同时确认门户真的在） -----------------
    try:
        _, page, _ = client.get(login_url)
        log("已打开登录页（{0} 字符）".format(len(page)))
    except Exception as error:
        return LoginResult(False, "portal_unreachable", "打不开登录页：{0}".format(error))

    if looks_like_online_page(page):
        return LoginResult(True, "already_online", "当前设备已经在线，无需登录")

    # ---- 2. 提交认证表单 -------------------------------------------------
    endpoint = build_login_endpoint(
        login_url,
        i_term_type=ITERM_TYPE_PC,
        login_method=LOGIN_METHOD_PORTAL,
        eportal_port=settings.eportal_port,
    )
    payload = build_login_payload(
        account=settings.account,
        password=password,
        operator=settings.operator,
        account_prefix=settings.account_prefix,
        i_term_type=ITERM_TYPE_PC,
    )

    log("提交认证请求：{0}".format(endpoint))
    log("账号：{0}（运营商后缀 {1}）".format(payload["DDDDD"], settings.operator or "无"))
    try:
        _, body, _ = client.post(endpoint, payload)
    except Exception as error:
        return LoginResult(False, "submit_failed", "提交认证请求失败：{0}".format(error))

    outcome: LoginOutcome = classify_response(body)
    log("门户返回判定：{0} / {1}".format(outcome.status, outcome.message))

    if outcome.ok:
        return LoginResult(True, "success", outcome.message, engine="http")

    if outcome.status == "bad_credentials":
        return LoginResult(False, "bad_credentials", outcome.message, engine="http")

    # ---- 3. 判定不了就复核一次（有些门户的成功页没有注释标记） -----------
    if verify and outcome.retryable:
        confirmed = verify_online(login_url, log=log)
        if confirmed is True:
            return LoginResult(True, "success", "复核确认已经在线", engine="http")
        if confirmed is False:
            return LoginResult(False, "failed", "提交后重新打开登录页，仍然要求登录", engine="http")

    return LoginResult(
        False, outcome.status if outcome.status != "unknown" else "failed", outcome.message,
        engine="http",
    )


def verify_online(login_url: str, log: Optional[Callable[[str], None]] = None) -> Optional[bool]:
    """复核：再打开一次登录页，看还是不是登录页。

    返回 True=已在线 / False=仍需登录 / None=无法判断。
    """
    log = log or _noop
    client = PortalClient(timeout=8.0)
    try:
        _, page, _ = client.get(login_url)
    except Exception as error:
        log("复核失败（{0}）".format(error))
        return None

    if looks_like_online_page(page) or not looks_like_login_page(page):
        return True
    return False
