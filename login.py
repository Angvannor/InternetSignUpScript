#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校园网自动登录工具 —— 主程序（命令行入口 + 主流程）。

常用命令
--------
    python login.py                  # 自动流程：没配置就进初始化，有配置就直接登录
    python login.py --setup          # 重新配置账号 / 密码 / 运营商，并立刻试登录一次
    python login.py --test           # 手动测试自动登录（显示完整过程）
    python login.py --probe          # 诊断：打印门户真实返回内容和运营商选项
    python login.py --status         # 看当前配置和是否在校园网
    python login.py --forget         # 删除已保存的密码
    python login.py --engine http    # 强制用 HTTP 引擎（不开浏览器）

退出码
------
    0  登录成功 / 本来就在线 / 不在校园网（安静退出，都算正常）
    1  尝试登录了但失败（账号密码错、门户改版等）
    2  配置缺失或用法错误
"""

from __future__ import annotations

import argparse
import copy
import getpass
import logging
import logging.handlers
import os
import sys
import time
import traceback
from typing import Callable, List, Optional

EXIT_OK = 0
EXIT_LOGIN_FAILED = 1
EXIT_USAGE = 2

LOGGER = logging.getLogger("campusnet")

BANNER = "=" * 46


# --------------------------------------------------------------------------
# 崩溃兜底
# --------------------------------------------------------------------------
#
# 开机自启走的是 start.bat -> pythonw.exe（没有控制台）。
# pythonw 会把 stderr 直接丢掉，所以一旦在导入模块或 main() 里抛异常，
# 表现就是"脚本好像没跑"，什么线索都没有（这次就是这么丢了 09-18 那次开机）。
# 因此这里自己把异常写进文件。

def _crash_log_path():
    from pathlib import Path

    base = os.environ.get("CAMPUSNET_CONFIG_DIR")
    if base:
        return Path(base) / "crash.log"
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if local:
        return Path(local) / "CampusNetAutoLogin" / "crash.log"
    return Path.home() / ".campusnetautologin" / "crash.log"


def _write_crash_log(text: str) -> None:
    """把崩溃信息追加到 crash.log；本身绝不能抛异常。"""
    try:
        path = _crash_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n===== {0} =====\n{1}\n".format(stamp, text))
    except Exception:
        pass


try:
    import config_store
    import drcom_portal
    import netcheck
    from config_store import PasswordStore, Settings, load_settings, save_settings
    from engine_http import LoginResult, login_with_http
    from engine_selenium import SeleniumUnavailable, login_with_selenium
except BaseException:  # pragma: no cover - 只在文件损坏/不完整时触发
    _write_crash_log(
        "导入模块失败（项目文件可能不完整或被占用）：\n{0}".format(traceback.format_exc())
    )
    raise


# --------------------------------------------------------------------------
# 日志
# --------------------------------------------------------------------------

def setup_logging(verbose: bool = False, quiet: bool = False) -> None:
    """日志同时写控制台和文件；**绝不记录密码**。"""
    LOGGER.setLevel(logging.DEBUG if verbose else logging.INFO)
    # 关掉旧 handler（重复调用 setup_logging 时不会泄漏日志文件句柄）
    for handler in list(LOGGER.handlers):
        LOGGER.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    try:
        path = config_store.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            str(path), maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        LOGGER.addHandler(file_handler)
    except Exception:
        pass  # 日志文件写不了也不该影响登录

    if not quiet:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter("%(message)s"))
        LOGGER.addHandler(console)

    if not LOGGER.handlers:
        LOGGER.addHandler(logging.NullHandler())


def _log(message: str) -> None:
    LOGGER.info(message)


# --------------------------------------------------------------------------
# 初始化向导
# --------------------------------------------------------------------------

def _ask(prompt: str, default: str = "") -> str:
    suffix = "（直接回车用默认值 {0}）".format(default) if default else ""
    try:
        answer = input("{0}{1}: ".format(prompt, suffix)).strip()
    except EOFError:
        return default
    return answer or default


def run_setup(
    settings: Settings,
    store: PasswordStore,
    interactive: bool = True,
    engine_override: Optional[str] = None,
) -> int:
    """第一次运行 / --setup 时执行：收账号、密码、运营商，保存，并试登录一次。"""
    LOGGER.info(BANNER)
    LOGGER.info("校园网自动登录初始化")
    LOGGER.info(BANNER)

    if settings.account:
        LOGGER.info("当前账号：{0}（运营商：{1}）".format(settings.account, settings.operator or "未设置"))

    # --- 账号 ---
    env_account = os.environ.get("CAMPUSNET_ACCOUNT")
    account = env_account or _ask("请输入校园网账号（学号/手机号）", settings.account)
    if not account:
        LOGGER.error("账号不能为空，配置未保存。")
        return EXIT_USAGE

    # --- 运营商 ---
    # 重要：三家运营商在校园网里是**各自独立的账号库**（账号后缀 @cmcc/@unicom/@telecom）。
    # 选错运营商的表现就是"账号密码明明是对的，门户却报账号或密码错误"。
    # 所以这里**故意不设默认值**，必须明确选一次 —— 早期版本默认按回车=中国移动，
    # 会让电信/联通的同学默默用错运营商。
    operator = _parse_operator_choice(os.environ.get("CAMPUSNET_OPERATOR", ""), settings)
    if not operator and interactive:
        LOGGER.info("请选择运营商（必选！三家是独立账号库，选错会报「账号或密码错误」）：")
        for index, name in enumerate(drcom_portal.ISP_CHOICES, start=1):
            LOGGER.info("  {0}. {1}（账号后缀 {2}）".format(
                index, name, drcom_portal.ISP_SUFFIX_BY_NAME[name]))
        if settings.operator:
            LOGGER.info("  （当前配置里是：{0}）".format(settings.operator))
        for _attempt in range(3):
            raw = _ask("请输入序号 1-3（不能直接回车）", "")
            operator = _parse_operator_choice(raw, settings)
            if operator:
                break
    if not operator:
        LOGGER.error("没有确定运营商，配置未保存。请重新运行：python login.py --setup")
        return EXIT_USAGE

    # --- 密码 ---
    env_password = os.environ.get("CAMPUSNET_PASSWORD")
    if env_password:
        password = env_password
    elif interactive:
        try:
            password = getpass.getpass("请输入校园网密码（输入时不显示）: ")
        except EOFError:
            password = ""
    else:
        password = ""
    if not password:
        LOGGER.error("密码不能为空，配置未保存。")
        return EXIT_USAGE

    settings.account = account
    settings.operator = operator
    settings.login_url = settings.login_url or drcom_portal.DEFAULT_LOGIN_URL

    # --- 登录地址（可选，但"抄别人机器上的地址"是很隐蔽的坑）--------------
    login_url = os.environ.get("CAMPUSNET_LOGIN_URL") or ""
    if not login_url and interactive:
        LOGGER.info("")
        LOGGER.info("登录地址（可以直接回车保持不变）：")
        LOGGER.info("  如果这份项目是从**别人电脑**上拷过来的，地址里就带着那台机器的")
        LOGGER.info("  认证参数（wlanuserip / wlanacip / wlanacname / mac），会导致登录失败。")
        LOGGER.info("  最保险的做法：用**这台电脑**的浏览器打开任意网页，被跳到认证页后，")
        LOGGER.info("  把地址栏里的完整地址（http://172.16.2.100/a70.htm?...）粘贴过来。")
        LOGGER.info("  当前地址：{0}".format(settings.login_url))
        login_url = _ask("粘贴本机浏览器里的登录地址", "")
    if login_url:
        if login_url.lower().startswith("http"):
            settings.login_url = login_url.strip()
            LOGGER.info("已使用你粘贴的登录地址。")
        else:
            LOGGER.warning("这不像一个网址，忽略了：{0}".format(login_url[:60]))

    # --- 密码自检：只报告"形状"，绝不打印密码内容 ------------------------
    LOGGER.info("密码自检：{0}".format(drcom_portal.password_fingerprint(password)))
    for warning in drcom_portal.password_warnings(password):
        LOGGER.warning("⚠ 密码警告：{0}".format(warning))

    if drcom_portal.looks_like_mobile(account):
        LOGGER.info(
            "提示：你填的是**手机号**。门户支持「学工号 / 手机号 / Email」登录，\n"
            "      但手机号必须先在自助服务平台绑定过才行。\n"
            "      如果登录失败，请改用**学号**再试一次（这是最常见的坑）。"
        )

    backend = store.save(password)
    saved_path = save_settings(settings)
    LOGGER.info("配置已保存到：{0}".format(saved_path))
    _warn_about_password_backend(backend)

    # --- 立刻试登录一次 ---
    LOGGER.info("")
    LOGGER.info("正在执行第一次登录测试…")
    return run_login(settings, store, engine_override=engine_override)


def _parse_operator_choice(raw: str, settings: Settings) -> str:
    """把 "1" / "中国移动" / "@cmcc" 统一成运营商名字（必要时透传未知后缀）。

    解析不出来时返回 ""，**绝不猜**——猜错运营商的后果是"账号密码没错却登录失败"。
    """
    raw = (raw or "").strip()
    if not raw:
        return ""

    if raw.isdigit():
        index = int(raw)
        if 1 <= index <= len(drcom_portal.ISP_CHOICES):
            return drcom_portal.ISP_CHOICES[index - 1]
        LOGGER.error("序号只能是 1、2、3，收到的是“{0}”。".format(raw))
        return ""

    if raw in drcom_portal.ISP_SUFFIX_BY_NAME:
        return raw

    if raw.startswith("@"):
        for name, suffix in drcom_portal.ISP_SUFFIX_BY_NAME.items():
            if suffix == raw:
                return name          # 规范成名字，方便日志和配置统一
        return raw                   # 学校若新增了别家运营商，原样透传

    # 简写（"移动"/"电信"）：必须唯一匹配，避免"中国"这种歧义输入默默选了第一家
    matches = [name for name in drcom_portal.ISP_CHOICES if raw in name or name in raw]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        LOGGER.error(
            "“{0}”同时匹配到 {1}，太含糊了，请直接输入 1、2 或 3。".format(
                raw, "、".join(matches))
        )
        return ""

    LOGGER.error(
        "无法识别的运营商“{0}”，请填 1/2/3，或 中国移动/中国联通/中国电信。".format(raw)
    )
    return ""


def _warn_about_password_backend(backend: str) -> None:
    if backend == "keyring":
        LOGGER.info("密码已存入 Windows 凭据管理器（最安全）。")
    elif backend == "dpapi":
        LOGGER.info("密码已用 Windows DPAPI 加密后保存在本机配置里（只有当前 Windows 用户能解开）。")
    else:
        LOGGER.warning(
            "警告：密码以**明文**保存在本机配置文件里（{0}）。\n"
            "      请确保这台电脑只有你自己使用。\n"
            "      想更安全请执行：pip install keyring".format(config_store.config_path())
        )


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def _resolve_engine(settings: Settings, override: Optional[str]) -> str:
    engine = (override or settings.engine or "auto").lower()
    if engine not in ("auto", "http", "selenium"):
        LOGGER.warning("未知引擎“{0}”，改用 auto。".format(engine))
        return "auto"
    return engine


def _selenium_importable() -> bool:
    try:
        import selenium  # noqa: F401
    except Exception:
        return False
    return True


def _try_engines(
    settings: Settings,
    password: str,
    login_url: str,
    engine: str,
) -> LoginResult:
    """按 engine 配置执行登录；auto = 先用 HTTP，失败且可能有用时再退到 Selenium。"""
    if engine in ("auto", "http"):
        result = login_with_http(settings, password, login_url, log=_log)
        if result.ok:
            return result
        if engine == "http":
            return result
        # auto：账号密码错就别再拿浏览器试一遍了（避免多次失败触发风控）
        if result.status == "bad_credentials":
            return result
        if not _selenium_importable():
            _log("HTTP 引擎没成功（{0}），且没有安装 selenium，无法回退到浏览器方式。".format(result.status))
            return result
        _log("HTTP 引擎没成功（{0}），改用 Selenium 再试一次…".format(result.status))

    try:
        return login_with_selenium(settings, password, login_url, log=_log)
    except SeleniumUnavailable as error:
        return LoginResult(False, "selenium_unavailable", str(error), engine="selenium")


def run_login(
    settings: Settings,
    store: PasswordStore,
    engine_override: Optional[str] = None,
) -> int:
    """完整的自动登录流程（需求文档第十六条）。"""
    engine = _resolve_engine(settings, engine_override)

    # ---- 1. 把登录地址里的 IP 换成当前网卡的真实 IP --------------------
    # 主机和端口一律从登录地址里取，这样 http://127.0.0.1:xxxx/ 这类地址也能用
    portal_host, portal_port = drcom_portal.portal_address(
        settings.login_url, settings.portal_host
    )
    local_ip = netcheck.guess_local_ip(portal_host)
    login_url = settings.login_url

    # 检测"这份配置是从别人电脑上抄来的"：地址里的 wlanuserip 既不是本机 IP，
    # 也不是本网段的样子 —— 说明那些认证参数属于另一台机器。
    configured_ip = drcom_portal.parse_login_url(settings.login_url).get("wlanuserip", "")
    if settings.autofill_client_ip and configured_ip and local_ip and configured_ip != local_ip:
        _log(
            "⚠ 注意：配置里的 wlanuserip 是 {0}，而本机网卡 IP 是 {1}。\n"
            "    这说明登录地址（连同 wlanacip / wlanacname / mac）很可能是从**别的电脑**上\n"
            "    抄过来的，那些认证参数属于那台机器，照抄会导致登录失败。\n"
            "    本次已自动按本机/门户的值修正；如果仍然失败，请运行\n"
            "        python login.py --setup\n"
            "    并把**本机浏览器**地址栏里那个完整的认证页地址粘贴进去。".format(
                configured_ip, local_ip
            )
        )

    if settings.autofill_client_ip and local_ip:
        login_url, changed = drcom_portal.autofill_client_ip(settings.login_url, local_ip)
        if changed:
            _log("已把登录地址里的 IP 参数更新为当前网卡 IP：{0}".format(local_ip))

    # ---- 2. 等网络 ------------------------------------------------------
    _log("等待网络连接（最多 {0} 秒）…".format(settings.wait_network_seconds))
    netcheck.wait_for_network(
        portal_host, portal_port, settings.wait_network_seconds, log=_log
    )

    # ---- 3. 判断是不是校园网 --------------------------------------------
    probe = netcheck.wait_for_campus(
        login_url, portal_host, settings.wait_network_seconds, log=_log
    )

    # ---- 3.5 用"认证服务器看到的 IP"再修正一次 -------------------------
    # 本机网卡 IP 不一定等于服务器看到的 IP（宿舍路由器做 NAT 时就不等）。
    # 门户页面里的 v46ip/ss5 就是服务器看到的值，以它为准才稳。
    if settings.autofill_client_ip and probe.html:
        ac_ip = drcom_portal.ac_reported_client_ip(probe.html)
        current_ip = drcom_portal.parse_login_url(login_url).get("wlanuserip", "")
        if ac_ip and ac_ip != current_ip:
            _log(
                "登录地址里的客户端 IP 已按门户报告的值修正：{0} → {1}".format(
                    current_ip or "空", ac_ip
                )
            )
            login_url, _ = drcom_portal.autofill_client_ip(login_url, ac_ip)
        if ac_ip and local_ip and ac_ip != local_ip:
            _log(
                "注意：本机网卡 IP 是 {0}，而门户看到的是 {1}，中间可能有路由器/NAT。"
                "已按门户报告的值提交（这是正确的做法）。".format(local_ip, ac_ip)
            )

    if probe.status == netcheck.STATUS_UNREACHABLE:
        _log("当前不在校园网环境（{0}），不做任何操作，退出。".format(probe.detail))
        return EXIT_OK
    if probe.status == netcheck.STATUS_ALREADY_ONLINE:
        _log("当前设备已经在线，无需登录。")
        return EXIT_OK
    if probe.status == netcheck.STATUS_ERROR:
        _log("认证页面异常：{0}，退出。".format(probe.detail))
        return EXIT_OK

    # ---- 4. 取密码 ------------------------------------------------------
    password = store.load()
    if not password:
        LOGGER.error("没有找到已保存的密码，请先运行：python login.py --setup")
        return EXIT_USAGE

    # ---- 5. 登录 --------------------------------------------------------
    attempt = 0
    result = LoginResult(False, "unknown", "未执行")
    while attempt <= max(0, settings.retry_times):
        attempt += 1
        _log("开始登录（第 {0} 次，引擎 {1}）…".format(attempt, engine))
        result = _try_engines(settings, password, login_url, engine)
        if result.ok or result.status == "bad_credentials":
            break
        if attempt <= settings.retry_times:
            _log("登录未成功（{0}），重试一次…".format(result.status))

    # ---- 6. 汇报 --------------------------------------------------------
    if result.ok:
        _log("登录成功：{0}（引擎 {1}）".format(result.detail, result.engine))
        return EXIT_OK

    LOGGER.error("登录失败：{0}".format(result.detail))
    if result.status == "bad_credentials":
        used = settings.operator or "未设置"
        suffix = drcom_portal.isp_suffix(settings.operator) or "无"
        LOGGER.error("本次用的运营商：{0}（账号后缀 {1}）".format(used, suffix))
        LOGGER.error(
            "提醒：三家运营商是**各自独立的账号库**。运营商选错时的表现就是\n"
            "      「账号密码明明没错，门户却报账号或密码错误」。请按顺序排查："
        )
        LOGGER.error("  1. 运营商选对了吗？      重选： python login.py --setup")
        LOGGER.error("  2. 不确定该选哪家？      自动判断： python login.py --check-operator")
        LOGGER.error("  3. 登录地址是不是从别人电脑抄来的？在**本机浏览器**里打开认证页，")
        LOGGER.error("     把地址栏里的完整地址粘到 python login.py --setup 里的登录地址")
        LOGGER.error("  4. 账号前后有没有多打空格？密码有没有全角字符/大小写问题？")
        LOGGER.error("  5. 先用浏览器打开登录页手动登一次：手动能登、工具不能，请把日志发我。")
    return EXIT_LOGIN_FAILED


# --------------------------------------------------------------------------
# 常驻监视模式（解决"睡醒/息屏后掉线要手动重登"）
# --------------------------------------------------------------------------

#: 监视循环里每一轮的状态
WATCH_ONLINE = "online"          # 已经在线
WATCH_LOGGED_IN = "logged_in"    # 刚刚登录成功
WATCH_FAILED = "failed"          # 在校园网里但登录失败
WATCH_NOT_CAMPUS = "not_campus"  # 不在校园网（在家 / 网卡没连上）
WATCH_NO_PASSWORD = "no_password"


def watch_tick(
    settings: Settings,
    store: PasswordStore,
    engine_override: Optional[str] = None,
) -> "tuple":
    """监视循环的一轮：探一次门户，需要登录就登录。

    刻意做得很轻：只发一个 GET 探测（不像 run_login 那样有 60 秒等待），
    这样每 30 秒跑一次也不会有负担。

    返回 (状态, 说明)。
    """
    portal_host, _ = drcom_portal.portal_address(settings.login_url, settings.portal_host)
    local_ip = netcheck.guess_local_ip(portal_host)
    login_url = settings.login_url
    if settings.autofill_client_ip and local_ip:
        login_url, _ = drcom_portal.autofill_client_ip(login_url, local_ip)

    probe = netcheck.probe_portal(login_url, timeout=6)

    if probe.status == netcheck.STATUS_UNREACHABLE:
        return WATCH_NOT_CAMPUS, probe.detail
    if probe.status == netcheck.STATUS_ALREADY_ONLINE:
        return WATCH_ONLINE, probe.detail

    # 睡眠唤醒、AC 空闲踢下线之后，这里会走到"要求登录"分支，于是自动重登
    if settings.autofill_client_ip:
        ac_ip = drcom_portal.ac_reported_client_ip(probe.html)
        if ac_ip:
            login_url, _ = drcom_portal.autofill_client_ip(login_url, ac_ip)

    password = store.load()
    if not password:
        return WATCH_NO_PASSWORD, "没有已保存的密码（请运行 python login.py --setup）"

    engine = _resolve_engine(settings, engine_override)
    result = _try_engines(settings, password, login_url, engine)
    if result.ok:
        return WATCH_LOGGED_IN, result.detail
    return WATCH_FAILED, result.detail


def run_watch(
    settings: Settings,
    store: PasswordStore,
    engine_override: Optional[str] = None,
    interval: float = 30.0,
    online_interval: Optional[float] = None,
    max_rounds: Optional[int] = None,
    sleep: Callable[[float], None] = time.sleep,
    tick: Optional[Callable[..., "tuple"]] = None,
) -> int:
    """常驻监视：隔一段时间探一次，掉线就自动重新登录。

    这是"息屏/睡眠后网络断开"的正解 —— Windows 的启动文件夹只在登录时跑一次，
    睡醒、锁屏解锁、AC 空闲踢人它都管不到；常驻进程天然覆盖这些情况。

    参数 sleep / tick / max_rounds 是为了能被单元测试注入，
    正常运行不用管。
    """
    tick = tick or watch_tick
    online_interval = online_interval if online_interval is not None else max(interval * 10, 300.0)

    LOGGER.info("进入常驻监视模式：掉线/睡醒后会自动重新登录。")
    LOGGER.info("  探测间隔：不在线时 {0:.0f} 秒，在线时 {1:.0f} 秒".format(interval, online_interval))
    LOGGER.info("  按 Ctrl+C 结束。")

    previous = None
    rounds = 0
    try:
        while True:
            rounds += 1
            try:
                state, detail = tick(settings, store, engine_override)
            except Exception:
                state, detail = WATCH_FAILED, "本轮异常"
                LOGGER.error("监视轮次异常（已忽略，继续监视）：\n{0}".format(traceback.format_exc()))

            if state != previous:
                if state == WATCH_LOGGED_IN:
                    LOGGER.info("✅ 已自动重新登录：{0}".format(detail))
                elif state == WATCH_ONLINE:
                    LOGGER.info("当前已在线，继续监视。")
                elif state == WATCH_NOT_CAMPUS:
                    LOGGER.info("不在校园网环境（{0}），继续等待。".format(detail))
                elif state == WATCH_NO_PASSWORD:
                    LOGGER.error("没有可用的密码：{0}".format(detail))
                else:
                    LOGGER.warning("在校园网里但登录失败：{0}".format(detail))
                previous = state
            elif state == WATCH_FAILED and rounds % 10 == 0:
                # 一直失败也要偶尔提醒，不然日志会太吵
                LOGGER.warning("仍在失败中：{0}".format(detail))

            if max_rounds is not None and rounds >= max_rounds:
                return EXIT_OK

            sleep(online_interval if state == WATCH_ONLINE else interval)
    except KeyboardInterrupt:
        LOGGER.info("监视已停止。")
        return EXIT_OK


# --------------------------------------------------------------------------
# 辅助命令
# --------------------------------------------------------------------------

def command_probe(settings: Settings) -> int:
    """诊断命令：把门户真实返回的东西打印出来，方便核对定位器/协议。"""
    portal_host, portal_port = drcom_portal.portal_address(
        settings.login_url, settings.portal_host
    )
    local_ip = netcheck.guess_local_ip(portal_host)
    login_url, changed = drcom_portal.autofill_client_ip(settings.login_url, local_ip)
    LOGGER.info("认证服务器：{0}:{1}".format(portal_host, portal_port))
    LOGGER.info("本机用于访问门户的 IP：{0}".format(local_ip or "未知"))
    LOGGER.info("登录地址：{0}{1}".format(login_url, "（已按当前 IP 修正）" if changed else ""))
    LOGGER.info("认证接口：{0}".format(
        drcom_portal.build_login_endpoint(login_url, eportal_port=settings.eportal_port)))

    probe = netcheck.probe_portal(login_url, timeout=10)
    LOGGER.info("探测结果：{0} —— {1}".format(probe.status, probe.detail))

    if not probe.html:
        LOGGER.error("拿不到页面内容，无法继续诊断（多半是不在校园网）。")
        return EXIT_OK

    # 关键诊断：本机网卡 IP 和"认证服务器看到的 IP"是否一致
    ac_ip = drcom_portal.ac_reported_client_ip(probe.html)
    LOGGER.info("认证服务器看到的客户端 IP：{0}".format(ac_ip or "（页面里没报告）"))
    if ac_ip and local_ip and ac_ip != local_ip:
        LOGGER.warning(
            "两者不一致！本机网卡是 {0}，服务器看到的是 {1}。\n"
            "    说明你和认证服务器之间还有一层路由器/NAT。\n"
            "    提交给门户的 wlanuserip 必须是服务器看到的那个，本工具已按此处理。".format(
                local_ip, ac_ip
            )
        )
    elif ac_ip:
        LOGGER.info("两者一致，网络路径上没有 NAT。")

    options = drcom_portal.parse_isp_options(probe.html)
    if options:
        LOGGER.info("页面上的运营商下拉框（select[name=ISP_select]）：")
        for text, value in options.items():
            LOGGER.info("    {0:<12} -> {1}".format(text, value))
    else:
        LOGGER.info(
            "首页 HTML 里没有直接的运营商下拉框：\n"
            "    这是正常的 —— 门户把界面放在 "
            "http://{0}:801/eportal/extern/<方案>/<页面类型>/<序号>/pc.js 里，\n"
            "    由 JavaScript 动态拼出来。本工具已从该模板中提取到真实选项：".format(
                settings.portal_host
            )
        )
        for text, value in drcom_portal.ISP_SUFFIX_BY_NAME.items():
            LOGGER.info("    {0:<12} -> {1}".format(text, value))

    outcome = drcom_portal.classify_response(probe.html)
    LOGGER.info("页面判定：{0} / {1}".format(outcome.status, outcome.message))
    LOGGER.info("页面特征：登录页={0} 在线页={1}".format(
        drcom_portal.looks_like_login_page(probe.html),
        drcom_portal.looks_like_online_page(probe.html),
    ))

    LOGGER.info("外网可达：{0}".format(netcheck.internet_reachable()))
    LOGGER.info("")
    LOGGER.info("提示：想确认 Selenium 定位器是否仍然有效，可在登录页按 F12，"
                "看 <input name=\"DDDDD\">、<input name=\"upass\">、"
                "<select name=\"ISP_select\">、<input name=\"0MKKey\"> 是否还在。")
    return EXIT_OK


def command_status(settings: Settings, store: PasswordStore) -> int:
    """打印当前配置和校园网状态。"""
    import json

    LOGGER.info("配置文件：{0}".format(config_store.config_path()))
    LOGGER.info("配置是否存在：{0}".format(config_store.config_exists()))
    LOGGER.info("密码是否已保存：{0}（后端：{1}）".format(
        store.load() is not None, store.resolve_backend()))
    LOGGER.info("当前配置（不含密码）：")
    LOGGER.info(json.dumps(settings.to_public_dict(), ensure_ascii=False, indent=2))

    local_ip = netcheck.guess_local_ip(settings.portal_host)
    login_url, _ = drcom_portal.autofill_client_ip(settings.login_url, local_ip)
    probe = netcheck.probe_portal(login_url, timeout=8)
    LOGGER.info("校园网状态：{0} —— {1}".format(probe.status, probe.detail))
    LOGGER.info("能上外网：{0}".format(netcheck.internet_reachable()))
    return EXIT_OK


def command_forget(settings: Settings, store: PasswordStore) -> int:
    store.delete()
    save_settings(settings)
    LOGGER.info("已删除本机保存的校园网密码。下次登录请运行 python login.py --setup")
    return EXIT_OK


def command_check_operator(settings: Settings, store: PasswordStore) -> int:
    """依次用三家运营商试登录，判断这个账号到底属于哪一家。

    这是"账号密码没错、门户却报账号或密码错误"最有效的排查手段：
    三家运营商是各自独立的账号库，选错了就一定会报错。

    代价：最多产生 3 次认证尝试。如果学校有"连续失败锁定账号"的策略，
    请在开始前确认风险，不要反复跑。
    """
    password = store.load()
    if not password:
        LOGGER.error("没有找到已保存的密码，请先运行：python login.py --setup")
        return EXIT_USAGE
    if not settings.has_account:
        LOGGER.error("还没有配置账号，请先运行：python login.py --setup")
        return EXIT_USAGE

    portal_host, _ = drcom_portal.portal_address(settings.login_url, settings.portal_host)
    local_ip = netcheck.guess_local_ip(portal_host)
    login_url, _ = drcom_portal.autofill_client_ip(settings.login_url, local_ip)

    probe = netcheck.probe_portal(login_url, timeout=8)
    if probe.status == netcheck.STATUS_UNREACHABLE:
        LOGGER.error("现在不在校园网环境（{0}），无法检测运营商。".format(probe.detail))
        return EXIT_OK
    if probe.status == netcheck.STATUS_ALREADY_ONLINE:
        LOGGER.info(
            "当前设备已经在线，没法再登录一次来试运营商。\n"
            "请先注销校园网（或在另一台还没登录的设备上跑），再来检测。"
        )
        return EXIT_OK

    # 先试当前配置里的那家（可能是对的），再试另外两家
    order = []
    if settings.operator:
        order.append(settings.operator)
    order += [name for name in drcom_portal.ISP_CHOICES if name != settings.operator]

    LOGGER.warning(
        "即将依次尝试 {0} 家运营商，最多产生 {0} 次认证请求。"
        "如果学校有连续失败锁定策略，请谨慎使用。".format(len(order))
    )

    for index, name in enumerate(order, start=1):
        suffix = drcom_portal.isp_suffix(name) or name
        account_preview = drcom_portal.build_account(
            settings.account, name, settings.account_prefix
        )
        LOGGER.info("[{0}/{1}] 试运营商：{2}（后缀 {3}，账号 {4}）".format(
            index, len(order), name, suffix, account_preview))

        trial = copy.deepcopy(settings)
        trial.operator = name
        result = login_with_http(trial, password, login_url, log=_log)

        if result.ok:
            LOGGER.info("")
            LOGGER.info("=" * 46)
            LOGGER.info("✅ 成功！这个账号属于：{0}".format(name))
            LOGGER.info("=" * 46)
            settings.operator = name
            save_settings(settings)
            LOGGER.info("已把配置里的运营商改成 {0}，以后直接 python login.py 就能自动登录。".format(name))
            return EXIT_OK

        LOGGER.info("    {0}".format(result.detail))

    LOGGER.error("")
    LOGGER.error("三家运营商都试过了，全部失败 —— 所以问题**不在运营商**，而在账号或密码本身：")
    LOGGER.error("  1. 账号填的是学号还是手机号？和你在浏览器里登录时用的完全一致吗？")
    LOGGER.error("  2. 账号前后有没有多余空格？密码有没有被中文输入法打成全角字符？")
    LOGGER.error("  3. 密码里的字母大小写？")
    LOGGER.error("  4. 用浏览器打开登录页手动登一次，确认这套账号密码本身能不能登上。")
    LOGGER.error("  5. 如果手动能登、工具不能，请把日志发我：{0}".format(config_store.log_path()))
    return EXIT_LOGIN_FAILED


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="login.py",
        description="校园网开机自动登录工具（Dr.COM / 城市热点 eportal）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python login.py                 自动流程\n"
            "  python login.py --setup         重新配置账号密码\n"
            "  python login.py --test          手动测试登录\n"
            "  python login.py --probe         诊断门户页面\n"
            "  python login.py --engine http   不开浏览器\n"
        ),
    )
    parser.add_argument("--setup", action="store_true", help="重新配置账号、密码、运营商")
    parser.add_argument("--test", action="store_true", help="手动测试自动登录（等于正常流程 + 详细输出）")
    parser.add_argument("--probe", action="store_true", help="诊断：打印门户真实返回内容和运营商选项")
    parser.add_argument("--status", action="store_true", help="显示当前配置与校园网状态")
    parser.add_argument("--forget", action="store_true", help="删除已保存的密码")
    parser.add_argument(
        "--check-operator",
        action="store_true",
        help="依次试三家运营商，判断账号属于哪家（排查「账号密码没错却报错」）",
    )
    parser.add_argument(
        "--engine", choices=("auto", "http", "selenium"),
        help="登录引擎：auto=先 HTTP 再退到 Selenium（默认）",
    )
    parser.add_argument(
        "--config",
        help="指定配置文件路径（默认 %%LOCALAPPDATA%%\\CampusNetAutoLogin\\config.json）",
    )
    parser.add_argument("--wait", type=int, help="等待网络/校园网的秒数")
    parser.add_argument(
        "--watch",
        action="store_true",
        help="常驻监视：掉线/睡醒后自动重新登录（解决息屏休眠后掉线的问题）",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="监视模式下不在线时的探测间隔秒数（默认 30，在线时自动放大到 5 分钟）",
    )
    parser.add_argument(
        "--online-interval",
        type=float,
        default=None,
        help="监视模式下已经在线时的探测间隔秒数（默认 300，即 5 分钟）",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=None,
        help=argparse.SUPPRESS,  # 仅供测试：跑够 N 轮就退出
    )
    parser.add_argument("--headless", action="store_true", help="Selenium 无界面运行 Edge")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    parser.add_argument("--quiet", action="store_true", help="不往控制台输出（只写日志文件），开机自启时用")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="绝不停下来提问（开机自启时用；没有配置就直接退出并记日志）",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(verbose=args.verbose, quiet=args.quiet)

    # 每次启动都留一条心跳。开机自启是没窗口的，出了事只能靠日志判断
    # "到底跑没跑起来"。有这一行就不会再出现"什么记录都没有"的情况。
    _log("启动：pid={0} 参数={1}".format(os.getpid(), " ".join(argv or sys.argv[1:]) or "(无)"))

    config_file = None
    if args.config:
        from pathlib import Path

        config_file = Path(args.config)

    try:
        settings = load_settings(config_file)
    except config_store.ConfigError as error:
        LOGGER.error("配置文件有问题：{0}".format(error))
        return EXIT_USAGE
    if args.wait is not None:
        settings.wait_network_seconds = max(0, args.wait)
    if args.headless:
        settings.headless = True

    store = PasswordStore(settings)

    # ---- 诊断类命令优先 ------------------------------------------------
    if args.probe:
        return command_probe(settings)
    if args.status:
        return command_status(settings, store)
    if args.forget:
        return command_forget(settings, store)
    if args.check_operator:
        return command_check_operator(settings, store)

    # ---- 需要交互的初始化 ----------------------------------------------
    # 开机自启（--quiet / --non-interactive）时绝不允许停在 input() 上等输入，
    # 否则在没有窗口的启动环境里会一直挂着。
    interactive = bool(sys.stdin is not None and sys.stdin.isatty())
    if args.non_interactive or args.quiet:
        interactive = False

    if args.setup or not settings.has_account:
        if not interactive and not (os.environ.get("CAMPUSNET_PASSWORD") and os.environ.get("CAMPUSNET_ACCOUNT")):
            LOGGER.error(
                "还没有配置账号，而当前不是交互式终端，无法提问。\n"
                "请先在有窗口的命令行里运行：python login.py --setup"
            )
            return EXIT_USAGE
        return run_setup(settings, store, interactive=interactive, engine_override=args.engine)

    # ---- 常驻监视模式 ---------------------------------------------------
    if args.watch:
        return run_watch(
            settings, store,
            engine_override=args.engine,
            interval=args.interval,
            online_interval=args.online_interval,
            max_rounds=args.max_rounds,
        )

    # ---- 正常登录 -------------------------------------------------------
    if args.test:
        LOGGER.info("== 手动测试模式 ==")
        LOGGER.info("如果这里显示“当前不在校园网环境”，说明你现在没连校园网（这是正常行为）。")
    try:
        return run_login(settings, store, engine_override=args.engine)
    except KeyboardInterrupt:
        LOGGER.warning("已手动中断。")
        return EXIT_LOGIN_FAILED


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        # pythonw 会吞掉 stderr，所以自己落盘
        _write_crash_log("main() 未捕获异常：\n{0}".format(traceback.format_exc()))
        raise
