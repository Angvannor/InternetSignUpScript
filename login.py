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
import getpass
import logging
import logging.handlers
import os
import sys
from typing import Callable, List, Optional

import config_store
import drcom_portal
import netcheck
from config_store import PasswordStore, Settings, load_settings, save_settings
from engine_http import LoginResult, login_with_http
from engine_selenium import SeleniumUnavailable, login_with_selenium

EXIT_OK = 0
EXIT_LOGIN_FAILED = 1
EXIT_USAGE = 2

LOGGER = logging.getLogger("campusnet")

BANNER = "=" * 46


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
    operator = os.environ.get("CAMPUSNET_OPERATOR")
    if not operator and interactive:
        LOGGER.info("请选择运营商：")
        for index, name in enumerate(drcom_portal.ISP_CHOICES, start=1):
            LOGGER.info("  {0}. {1}（账号后缀 {2}）".format(
                index, name, drcom_portal.ISP_SUFFIX_BY_NAME[name]))
        raw = _ask("请输入序号 1-3", settings.operator or "1")
        operator = _parse_operator_choice(raw, settings)
    if not operator:
        LOGGER.error("运营商不能为空，配置未保存。")
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

    backend = store.save(password)
    saved_path = save_settings(settings)
    LOGGER.info("配置已保存到：{0}".format(saved_path))
    _warn_about_password_backend(backend)

    # --- 立刻试登录一次 ---
    LOGGER.info("")
    LOGGER.info("正在执行第一次登录测试…")
    return run_login(settings, store, engine_override=engine_override)


def _parse_operator_choice(raw: str, settings: Settings) -> str:
    """把 "1" / "中国移动" / "@cmcc" 统一成运营商名字或后缀。"""
    raw = (raw or "").strip()
    if raw.isdigit():
        index = int(raw)
        if 1 <= index <= len(drcom_portal.ISP_CHOICES):
            return drcom_portal.ISP_CHOICES[index - 1]
    if raw in drcom_portal.ISP_SUFFIX_BY_NAME:
        return raw
    if raw.startswith("@"):
        return raw
    # 允许输入 "移动" 这种简写
    for name in drcom_portal.ISP_CHOICES:
        if raw and (raw in name or name in raw):
            return name
    LOGGER.error("无法识别的运营商“{0}”，请填 1/2/3 或 中国移动/中国联通/中国电信。".format(raw))
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
        LOGGER.error("请检查账号密码是否正确，或执行 python login.py --setup 重新配置。")
    return EXIT_LOGIN_FAILED


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
        "--engine", choices=("auto", "http", "selenium"),
        help="登录引擎：auto=先 HTTP 再退到 Selenium（默认）",
    )
    parser.add_argument(
        "--config",
        help="指定配置文件路径（默认 %%LOCALAPPDATA%%\\CampusNetAutoLogin\\config.json）",
    )
    parser.add_argument("--wait", type=int, help="等待网络/校园网的秒数")
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

    config_file = None
    if args.config:
        from pathlib import Path

        config_file = Path(args.config)

    settings = load_settings(config_file)
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
    sys.exit(main())
