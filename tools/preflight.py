"""启动前自检：确认项目里每个模块都能正常导入。

为什么需要这个文件
------------------
开机自启走的是 start.bat -> pythonw.exe，而 pythonw **没有控制台**，
它会把 stderr 直接丢掉。于是只要有一个文件有语法错误/导入错误，
表现就是"脚本好像根本没跑"——日志里连一行都不会有，完全无从查起。

2026-09-18 就是这么丢了当天那次开机登录：
engine_http.py 的第一行被多写了一个 "py"（`py#!/usr/bin/env python`），
import 时直接 SyntaxError，而 pythonw 把错误信息扔了。

这个自检用一次极短的导入把所有问题暴露出来，并把详情写进
%LOCALAPPDATA%\\CampusNetAutoLogin\\start_error.log。

用法：
    python tools/preflight.py      # 全部正常 -> 退出码 0；有问题 -> 1，并写日志
"""

from __future__ import annotations

import importlib
import os
import pathlib
import sys
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: 按依赖顺序导入；login 放最后（它会把这些都引一遍）
MODULES = (
    "drcom_portal",
    "config_store",
    "netcheck",
    "engine_http",
    "engine_selenium",
    "login",
)


def start_error_log_path() -> pathlib.Path:
    """自检失败写这里（和 start.bat 里找不到 Python 时用的是同一个文件）。"""
    override = os.environ.get("CAMPUSNET_CONFIG_DIR")
    if override:
        return pathlib.Path(override) / "start_error.log"
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return pathlib.Path(base) / "CampusNetAutoLogin" / "start_error.log"
    return pathlib.Path.home() / ".campusnetautologin" / "start_error.log"


def check_modules(path=None) -> "list":
    """导入每个模块，返回失败说明的列表（空列表 = 全部正常）。

    注意要自己清掉 sys.modules 缓存并让 root 排在 sys.path 最前面：
    否则在一个已经导入过这些模块的进程里（比如单元测试）检查的是**旧文件**，
    坏文件会被漏掉。正常使用时它是独立进程，本来就干净。
    """
    root = pathlib.Path(path) if path else ROOT
    root_str = str(root)

    while root_str in sys.path:
        sys.path.remove(root_str)
    sys.path.insert(0, root_str)
    importlib.invalidate_caches()

    failures = []
    for name in MODULES:
        sys.modules.pop(name, None)
        try:
            importlib.import_module(name)
        except BaseException:  # SyntaxError 也算，必须全抓
            failures.append("模块 {0} 导入失败：\n{1}".format(name, traceback.format_exc()))
    return failures


def write_failure_log(failures, log_path=None) -> pathlib.Path:
    path = pathlib.Path(log_path) if log_path else start_error_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n===== {0} 启动前自检失败 =====\n{1}\n".format(
                    time.strftime("%Y-%m-%d %H:%M:%S"), "\n".join(failures)
                )
            )
    except Exception:
        pass
    return path


def main(argv=None) -> int:
    failures = check_modules()
    if not failures:
        print("[preflight] OK - 所有模块都能正常导入")
        return 0

    text = "\n".join(failures)
    print("[preflight] FAILED\n{0}".format(text))
    path = write_failure_log(failures)
    print("[preflight] 详情已写入：{0}".format(path))
    return 1


if __name__ == "__main__":
    sys.exit(main())
