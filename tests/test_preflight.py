#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""启动前自检（tools/preflight.py）的测试。

背景：2026-09-18 那次开机没登录，是因为 engine_http.py 第一行被多写了一个
"py"，import 时 SyntaxError，而 pythonw 把 stderr 吞了 —— 日志里一个字都没有。
这个自检就是专门用来把这类"静默失败"变成"日志里一条明确的错误"。
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"
))

import preflight  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestPreflightOnRealProject(unittest.TestCase):
    def test_the_real_project_passes(self):
        self.assertEqual(preflight.check_modules(REPO_ROOT), [])

    def test_main_returns_zero_on_success(self):
        self.assertEqual(preflight.main(), 0)


class TestPreflightDetectsCorruption(unittest.TestCase):
    """把项目复制一份，人为写坏文件，自检必须报出来。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="campusnet-preflight-")
        self._old = os.environ.get("CAMPUSNET_CONFIG_DIR")
        os.environ["CAMPUSNET_CONFIG_DIR"] = self.tmp
        for name in preflight.MODULES:
            shutil.copy(os.path.join(REPO_ROOT, name + ".py"), self.tmp)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CAMPUSNET_CONFIG_DIR", None)
        else:
            os.environ["CAMPUSNET_CONFIG_DIR"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _check(self):
        """只让自检看到这份临时副本，别把真项目当后备路径找回来。"""
        saved = list(sys.path)
        try:
            sys.path[:] = [
                p for p in sys.path
                if os.path.abspath(p or ".") != os.path.abspath(REPO_ROOT)
            ]
            return preflight.check_modules(self.tmp)
        finally:
            sys.path[:] = saved

    def test_clean_copy_passes(self):
        self.assertEqual(self._check(), [])

    def test_stray_prefix_on_line_one_is_caught(self):
        """精确复现 09-18 那次故障：第一行前面多了 "py"。"""
        target = os.path.join(self.tmp, "engine_http.py")
        with open(target, "r", encoding="utf-8") as handle:
            source = handle.read()
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("py" + source)

        failures = self._check()
        self.assertTrue(failures, "第一行被写坏必须被发现")
        self.assertIn("engine_http", "\n".join(failures))
        self.assertIn("SyntaxError", "\n".join(failures))

    def test_missing_file_is_caught(self):
        os.remove(os.path.join(self.tmp, "netcheck.py"))
        failures = self._check()
        self.assertIn("netcheck", "\n".join(failures))

    def test_zero_byte_file_is_caught(self):
        with open(os.path.join(self.tmp, "drcom_portal.py"), "w", encoding="utf-8"):
            pass
        failures = self._check()
        self.assertIn("drcom_portal", "\n".join(failures))

    def test_failure_is_written_to_a_log_file(self):
        target = os.path.join(self.tmp, "engine_http.py")
        with open(target, "r", encoding="utf-8") as handle:
            source = handle.read()
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("py" + source)

        failures = self._check()
        log = preflight.write_failure_log(failures)
        self.assertTrue(os.path.exists(log))
        with open(log, "r", encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("启动前自检失败", text)
        self.assertIn("SyntaxError", text)

    def test_reports_every_broken_module_not_just_the_first(self):
        for name in ("engine_http", "netcheck"):
            target = os.path.join(self.tmp, name + ".py")
            with open(target, "r", encoding="utf-8") as handle:
                source = handle.read()
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("py" + source)
        joined = "\n".join(self._check())
        self.assertIn("engine_http", joined)
        self.assertIn("netcheck", joined)


class TestStartBatWiring(unittest.TestCase):
    def _read(self, name):
        with open(os.path.join(REPO_ROOT, name), "r", encoding="ascii") as handle:
            return handle.read()

    def test_start_bat_runs_preflight_before_login(self):
        text = self._read("start.bat")
        self.assertIn("preflight.py", text)
        self.assertLess(
            text.index("preflight.py"), text.index("login.py"),
            "自检必须在真正登录之前跑",
        )

    def test_watcher_bat_also_runs_preflight(self):
        self.assertIn("preflight.py", self._read("start_watch.bat"))

    def test_watcher_bat_starts_the_watch_mode(self):
        self.assertIn("--watch", self._read("start_watch.bat"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
