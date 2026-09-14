#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Selenium 4 + Microsoft Edge 登录引擎（需求文档第十一、十二、十三条）。

定位器全部来自门户自己吐出来的真实 HTML（见 drcom_portal.py 顶部注释），
不是猜出来的；同时为每个元素准备了一组"后备定位器"，
万一学校改了模板也能尽量自己找到。

    <form name="f3" onsubmit="return ee(3)">
        <input type="text"     name="DDDDD" placeholder="学号">
        <input type="password" name="upass" placeholder="密码">
        <input type="submit"   name="0MKKey" value="登 录">
    </form>
    <select name="ISP_select"> ... </select>
"""

from __future__ import annotations

import time
from typing import Callable, List, Optional, Sequence, Tuple

from drcom_portal import (
    FIELD_ACCOUNT,
    FIELD_ISP_SELECT,
    FIELD_LOGIN_BUTTON,
    FIELD_LOGIN_BUTTON_SMS,
    FIELD_PASSWORD,
    ISP_UNSELECTED_VALUE,
    LoginOutcome,
    classify_response,
    looks_like_online_page,
)
from config_store import Settings
from engine_http import LoginResult

#: 元素定位候选列表：第一个是门户真实结构，后面的是容错后备
LOCATORS_ACCOUNT: Sequence[Tuple[str, str]] = (
    ("name", FIELD_ACCOUNT),
    ("css selector", "input[name='{0}']".format(FIELD_ACCOUNT)),
    ("css selector", "input[placeholder='学号']"),
    ("css selector", "form[name='f3'] input[type='text']"),
    ("css selector", "form[name='f1'] input[type='text']"),
    ("css selector", "input[type='text']"),
)

LOCATORS_PASSWORD: Sequence[Tuple[str, str]] = (
    ("name", FIELD_PASSWORD),
    ("css selector", "input[name='{0}']".format(FIELD_PASSWORD)),
    ("css selector", "input[placeholder='密码']"),
    ("css selector", "input[type='password']"),
)

LOCATORS_ISP: Sequence[Tuple[str, str]] = (
    ("name", FIELD_ISP_SELECT),
    ("css selector", "select[name='{0}']".format(FIELD_ISP_SELECT)),
    ("css selector", "select.edit_select"),
    ("css selector", "select"),
)

LOCATORS_LOGIN_BUTTON: Sequence[Tuple[str, str]] = (
    ("name", FIELD_LOGIN_BUTTON),
    ("css selector", "input[name='{0}']".format(FIELD_LOGIN_BUTTON)),
    ("css selector", "form[name='f3'] input[type='submit']"),
    ("css selector", "input[type='submit'][value*='登']"),
    ("css selector", "input[type='submit']"),
)


def _noop(message: str) -> None:
    pass


class SeleniumUnavailable(RuntimeError):
    """selenium 没装或 Edge 起不来。"""


# --------------------------------------------------------------------------
# 驱动
# --------------------------------------------------------------------------

def _build_driver(settings: Settings, log: Callable[[str], None]):
    """创建 Edge 驱动。Selenium 4 自带 Selenium Manager，会自动配好 msedgedriver。"""
    try:
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
    except ImportError as error:
        raise SeleniumUnavailable(
            "没有安装 selenium（{0}）。请先执行：pip install -r requirements.txt".format(error)
        )

    options = Options()
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--window-size=1200,820")
    options.add_argument("--lang=zh-CN")
    if settings.headless:
        options.add_argument("--headless=new")

    log("启动 Microsoft Edge（Selenium Manager 会自动准备 EdgeDriver）…")
    try:
        driver = webdriver.Edge(options=options)
    except Exception as error:
        raise SeleniumUnavailable(
            "Edge 启动失败：{0}\n"
            "请确认已安装 Microsoft Edge；若首次运行需要联网下载 EdgeDriver，"
            "也可以改用 HTTP 引擎：python login.py --engine http".format(error)
        )

    driver.set_page_load_timeout(max(15, int(settings.wait_page_seconds) * 2))
    return driver


def _find_any(driver, candidates: Sequence[Tuple[str, str]], timeout: float):
    """按候选顺序找元素，找到就返回；都找不到返回 None。"""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    by_map = {"name": By.NAME, "css selector": By.CSS_SELECTOR}
    for how, what in candidates:
        try:
            return WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((by_map[how], what))
            )
        except Exception:
            continue
    return None


def _find_first_present(driver, candidates: Sequence[Tuple[str, str]]):
    """不等待，立即找第一个存在的元素（用于判断某个元素还在不在）。"""
    from selenium.webdriver.common.by import By

    by_map = {"name": By.NAME, "css selector": By.CSS_SELECTOR}
    for how, what in candidates:
        try:
            elements = driver.find_elements(by_map[how], what)
        except Exception:
            continue
        if elements:
            return elements[0]
    return None


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def login_with_selenium(
    settings: Settings,
    password: str,
    login_url: str,
    log: Optional[Callable[[str], None]] = None,
) -> LoginResult:
    """打开 Edge -> 填账号 -> 填密码 -> 选运营商 -> 点登录 -> 判断结果 -> 关浏览器。"""
    log = log or _noop
    driver = _build_driver(settings, log)

    try:
        return _run_flow(driver, settings, password, login_url, log)
    finally:
        try:
            driver.quit()
            log("已关闭 Edge。")
        except Exception:
            pass


def _run_flow(driver, settings: Settings, password: str, login_url: str, log) -> LoginResult:
    from selenium.webdriver.support.ui import Select

    log("打开登录页：{0}".format(login_url))
    driver.get(login_url)

    if looks_like_online_page(driver.page_source):
        return LoginResult(True, "already_online", "当前设备已经在线，无需登录", engine="selenium")

    # ---- 账号 ---------------------------------------------------------
    account_box = _find_any(driver, LOCATORS_ACCOUNT, settings.wait_page_seconds)
    if account_box is None:
        return LoginResult(
            False, "no_account_input",
            "找不到账号输入框（期望 input[name='DDDDD']）。请用 python login.py --probe 导出页面后反馈。",
            engine="selenium",
        )
    account_box.clear()
    account_box.send_keys(settings.account)
    log("已填写账号：{0}".format(settings.account))

    # ---- 密码 ---------------------------------------------------------
    password_box = _find_any(driver, LOCATORS_PASSWORD, settings.wait_page_seconds)
    if password_box is None:
        return LoginResult(
            False, "no_password_input",
            "找不到密码输入框（期望 input[name='upass']）。",
            engine="selenium",
        )
    password_box.clear()
    password_box.send_keys(password)
    log("已填写密码。")

    # ---- 运营商 -------------------------------------------------------
    isp_select = _find_any(driver, LOCATORS_ISP, 3)
    if isp_select is not None:
        picked = _select_operator(driver, isp_select, settings.operator, Select, log)
        if picked is False:
            return LoginResult(
                False, "no_operator",
                "运营商下拉框里没有找到“{0}”，请用 --probe 查看学校提供的选项。".format(settings.operator),
                engine="selenium",
            )
    else:
        log("页面上没有运营商下拉框（select[name='ISP_select']），跳过。")

    # ---- 「我已阅读」勾选框（门户配置 pg_checkIRead=1 时默认已勾选） ----
    _ensure_checkboxes_checked(driver, log)

    # ---- 登录按钮 -----------------------------------------------------
    login_button = _find_any(driver, LOCATORS_LOGIN_BUTTON, settings.wait_page_seconds)
    if login_button is None:
        return LoginResult(
            False, "no_login_button",
            "找不到登录按钮（期望 input[name='0MKKey']）。",
            engine="selenium",
        )

    if not login_button.is_enabled():
        log("登录按钮当前是禁用状态，等待它可用…")
        _wait_until(lambda: login_button.is_enabled(), 5)

    log("点击登录按钮。")
    login_button.click()

    # ---- 判断结果 -----------------------------------------------------
    outcome = _wait_login_result(driver, settings.wait_result_seconds, log)
    if outcome.ok:
        return LoginResult(True, "success", outcome.message, engine="selenium")
    return LoginResult(False, outcome.status, outcome.message, engine="selenium")


def _select_operator(driver, isp_select, operator: str, Select, log) -> Optional[bool]:
    """选择运营商；成功返回 True，找不到该选项返回 False，未配置返回 None。"""
    from drcom_portal import ISP_SUFFIX_BY_NAME, isp_suffix

    if not operator:
        log("没有配置运营商，保持下拉框默认值（可能是“选择运营商”，将导致登录失败）。")
        return None

    select = Select(isp_select)
    texts = [option.text.strip() for option in select.options]
    log("页面运营商选项：{0}".format("、".join(t for t in texts if t)))

    # 1) 先按显示文字选（需求文档要求的方式）
    try:
        select.select_by_visible_text(operator)
        log("已选择运营商：{0}".format(operator))
        return True
    except Exception:
        pass

    # 2) 再按 value（@cmcc / @unicom / @telecom）选
    wanted_value = isp_suffix(operator) or ISP_SUFFIX_BY_NAME.get(operator, "")
    if wanted_value:
        try:
            select.select_by_value(wanted_value)
            log("已按 value 选择运营商：{0} -> {1}".format(operator, wanted_value))
            return True
        except Exception:
            pass

    # 3) 模糊匹配（学校改成了“移动/中国移动(CMCC)”这类写法时兜底）
    for option in select.options:
        if operator in option.text or option.text.strip() in operator:
            select.select_by_visible_text(option.text)
            log("已模糊匹配运营商：{0} -> {1}".format(operator, option.text.strip()))
            return True

    return False


def _ensure_checkboxes_checked(driver, log) -> None:
    """把「我已阅读」之类的勾选框勾上，否则登录按钮可能是灰的。"""
    from selenium.webdriver.common.by import By

    try:
        boxes = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
    except Exception:
        return
    for box in boxes:
        try:
            if box.is_displayed() and not box.is_selected():
                box.click()
                log("已勾选页面上的一个复选框（如《我已阅读》）。")
        except Exception:
            continue


def _wait_until(predicate, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _wait_login_result(driver, seconds: float, log) -> LoginOutcome:
    """等待并判断登录结果。

    判定依据（都来自真实门户，见需求文档第十二条）：
      * 出现成功页注释 Dr.COMWebLoginID_3.htm / 注销按钮   -> 成功
      * 出现账号密码错误 / Msg=01                          -> 失败
      * 登录表单消失（账号框不见了）                        -> 成功
    只用显式轮询 + WebDriverWait，不靠固定 sleep 猜时间。
    """
    deadline = time.monotonic() + max(3.0, seconds)
    last_outcome = LoginOutcome("unknown", False, None, "超时：没有观察到明确的登录结果")

    while time.monotonic() < deadline:
        try:
            source = driver.page_source
        except Exception:
            # 页面正在跳转时可能读不到，稍后再读
            time.sleep(0.4)
            continue

        outcome = classify_response(source)
        if outcome.ok or outcome.status == "bad_credentials":
            return outcome
        if outcome.status == "failed":
            last_outcome = outcome

        if looks_like_online_page(source):
            return LoginOutcome("success", True, "00", "页面出现注销入口，判定为在线")

        # 登录表单消失了，通常说明已经跳转到成功页
        if _find_first_present(driver, LOCATORS_ACCOUNT) is None and "upass" not in source:
            return LoginOutcome("success", True, "00", "登录表单已消失，判定为登录成功")

        time.sleep(0.5)

    return last_outcome
