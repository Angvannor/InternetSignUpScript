#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""配置与密码的安全保存。

设计要点（对应需求文档第五、六、七、八条）：

* 账号、运营商、登录地址 -> 本地 JSON 配置
* 密码                    -> 优先 Windows 凭据管理器（keyring）
                             没有 keyring 时退化为 Windows DPAPI 加密后存本地
                             再退化为明文（会明确警告用户）
* 配置文件放在 %LOCALAPPDATA%\\CampusNetAutoLogin\\ 下，
  **根本不在 Git 仓库里**，所以不可能被误提交。
  （仓库里同时提供 .gitignore 作为第二道保险。）

为什么不用 keyring 也能跑：
  校园网没登录时是上不了外网的，也就 pip install 不了任何东西。
  所以本工具在"零第三方依赖"的情况下必须能完成登录，
  密码保存于是提供了纯标准库（ctypes + DPAPI）的实现。
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from ctypes import wintypes
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from drcom_portal import (
    ACCOUNT_PREFIX_PC,
    DEFAULT_LOGIN_URL,
    DEFAULT_PORTAL_HOST,
    EPORTAL_PORT,
)

APP_NAME = "CampusNetAutoLogin"
KEYRING_SERVICE = "CampusNetAutoLogin"

#: 密码保存方式：auto / keyring / dpapi / plain
PASSWORD_BACKENDS = ("auto", "keyring", "dpapi", "plain")


# --------------------------------------------------------------------------
# 配置文件位置
# --------------------------------------------------------------------------

def config_dir() -> Path:
    """返回配置文件所在目录（Windows 下是 %LOCALAPPDATA%\\CampusNetAutoLogin）。"""
    override = os.environ.get("CAMPUSNET_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / (".{0}".format(APP_NAME.lower()))


def config_path() -> Path:
    return config_dir() / "config.json"


def log_path() -> Path:
    return config_dir() / "login.log"


# --------------------------------------------------------------------------
# 配置数据
# --------------------------------------------------------------------------

@dataclass
class Settings:
    """用户配置。除了密码，其它都在这里。"""

    # --- 门户 ---
    login_url: str = DEFAULT_LOGIN_URL
    portal_host: str = DEFAULT_PORTAL_HOST
    #: eportal 认证接口端口（门户页面用的是 801）
    eportal_port: int = EPORTAL_PORT
    #: 每次运行把 URL 里的 wlanuserip / ip 换成当前网卡 IP
    autofill_client_ip: bool = True

    # --- 账号 ---
    account: str = ""
    operator: str = ""          # 中国移动 / 中国联通 / 中国电信
    account_prefix: str = ACCOUNT_PREFIX_PC

    # --- 运行方式 ---
    engine: str = "auto"        # auto / http / selenium
    wait_network_seconds: int = 60
    wait_page_seconds: int = 15
    wait_result_seconds: int = 20
    retry_times: int = 1
    headless: bool = False      # Selenium 是否无界面运行

    # --- 密码 ---
    password_backend: str = "auto"   # auto / keyring / dpapi / plain
    password_dpapi: str = ""         # DPAPI 密文（base64）
    password_plain: str = ""         # 明文兜底（会警告）

    # --- 其它 ---
    log_level: str = "INFO"
    extra: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @property
    def has_account(self) -> bool:
        return bool(self.account.strip())

    def to_public_dict(self) -> Dict[str, Any]:
        """给 --status / 日志用的、**不含任何密码字段**的字典。"""
        data = asdict(self)
        for key in ("password_dpapi", "password_plain"):
            if data.get(key):
                data[key] = "<已保存>"
        data["_config_path"] = str(config_path())
        return data


# --------------------------------------------------------------------------
# DPAPI（纯标准库，用 Windows 自带的加密 API 保护密码）
# --------------------------------------------------------------------------

class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_available() -> bool:
    return sys.platform == "win32"


def _blob_from_bytes(data: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _bytes_from_blob(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def dpapi_encrypt(plaintext: str) -> str:
    """用当前 Windows 用户的凭据加密，返回 base64 字符串。

    加密后的密文只有**同一个 Windows 用户**能解开 ——
    就算把配置文件拷给室友，他也读不出你的密码。
    """
    if not dpapi_available():
        raise RuntimeError("DPAPI 只在 Windows 上可用")

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    blob_in = _blob_from_bytes(plaintext.encode("utf-8"))
    blob_out = _DataBlob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in), "CampusNetAutoLogin", None, None, None, 0x01,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return base64.b64encode(_bytes_from_blob(blob_out)).decode("ascii")
    finally:
        kernel32.LocalFree(blob_out.pbData)


def dpapi_decrypt(ciphertext_b64: str) -> str:
    """解开 DPAPI 密文。"""
    if not dpapi_available():
        raise RuntimeError("DPAPI 只在 Windows 上可用")

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    raw = base64.b64decode(ciphertext_b64)
    blob_in = _blob_from_bytes(raw)
    blob_out = _DataBlob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0x01, ctypes.byref(blob_out)
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return _bytes_from_blob(blob_out).decode("utf-8")
    finally:
        kernel32.LocalFree(blob_out.pbData)


# --------------------------------------------------------------------------
# 密码存取
# --------------------------------------------------------------------------

def keyring_available() -> bool:
    try:
        import keyring  # noqa: F401
    except Exception:
        return False
    return True


class PasswordStore:
    """密码的保存/读取/删除，自动在 keyring / DPAPI / 明文之间选择。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    # -- 选择后端 ------------------------------------------------------
    def resolve_backend(self) -> str:
        wanted = (self.settings.password_backend or "auto").lower()
        if wanted in ("keyring", "dpapi", "plain"):
            if wanted == "keyring" and not (keyring_available() and self._keyring_usable()):
                return "dpapi" if dpapi_available() else "plain"
            return wanted
        # auto
        if keyring_available() and self._keyring_usable():
            return "keyring"
        if dpapi_available():
            return "dpapi"
        return "plain"

    def _keyring_usable(self) -> bool:
        """keyring 装了不代表能用（Linux 上可能没有后端），这里试一次。"""
        try:
            import keyring

            keyring.get_password(KEYRING_SERVICE, "__probe__")
            return True
        except Exception:
            return False

    # -- 读写 ----------------------------------------------------------
    def save(self, password: str) -> str:
        """保存密码，返回实际使用的后端名。"""
        backend = self.resolve_backend()

        if backend == "keyring":
            try:
                import keyring

                keyring.set_password(KEYRING_SERVICE, self.settings.account, password)
                self.settings.password_dpapi = ""
                self.settings.password_plain = ""
                return "keyring"
            except Exception:
                backend = "dpapi" if dpapi_available() else "plain"

        if backend == "dpapi":
            self.settings.password_dpapi = dpapi_encrypt(password)
            self.settings.password_plain = ""
            return "dpapi"

        self.settings.password_plain = password
        self.settings.password_dpapi = ""
        return "plain"

    def load(self) -> Optional[str]:
        """读取密码；读不到返回 None。"""
        # 1) 凭据管理器
        if keyring_available() and self.settings.account:
            try:
                import keyring

                found = keyring.get_password(KEYRING_SERVICE, self.settings.account)
                if found:
                    return found
            except Exception:
                pass

        # 2) DPAPI 密文
        if self.settings.password_dpapi:
            try:
                return dpapi_decrypt(self.settings.password_dpapi)
            except Exception:
                return None

        # 3) 明文兜底
        if self.settings.password_plain:
            return self.settings.password_plain

        return None

    def delete(self) -> None:
        """清掉已保存的密码（--forget 用）。"""
        if keyring_available() and self.settings.account:
            try:
                import keyring

                keyring.delete_password(KEYRING_SERVICE, self.settings.account)
            except Exception:
                pass
        self.settings.password_dpapi = ""
        self.settings.password_plain = ""


# --------------------------------------------------------------------------
# 读写配置文件
# --------------------------------------------------------------------------

class ConfigError(Exception):
    """配置文件读不了 / 不是合法 JSON。"""


def load_settings(path: Optional[Path] = None) -> Settings:
    """读取配置；文件不存在时返回默认配置。

    编码用 utf-8-sig：Windows 记事本另存为 UTF-8 时会加 BOM，
    用普通 utf-8 读会直接抛 JSONDecodeError（"Unexpected UTF-8 BOM"）。
    """
    target = Path(path) if path else config_path()
    if not target.exists():
        return Settings()

    try:
        with target.open("r", encoding="utf-8-sig") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as error:
        raise ConfigError(
            "配置文件不是合法的 JSON：{0}（第 {1} 行第 {2} 列）\n"
            "  文件：{3}\n"
            "  修好它，或者删掉这个文件后重新运行：python login.py --setup".format(
                error.msg, error.lineno, error.colno, target
            )
        )
    except OSError as error:
        raise ConfigError("读不了配置文件 {0}：{1}".format(target, error))

    if not isinstance(raw, dict):
        raise ConfigError("配置文件的内容应该是一个 JSON 对象，实际是 {0}".format(type(raw).__name__))

    known = {f for f in Settings.__dataclass_fields__}  # type: ignore[attr-defined]
    data = {k: v for k, v in raw.items() if k in known}
    settings = Settings(**data)
    # 把不认识的键留在 extra 里，避免升级后丢配置
    settings.extra = {k: v for k, v in raw.items() if k not in known}
    return settings


def save_settings(settings: Settings, path: Optional[Path] = None) -> Path:
    """写配置（会自动创建目录，并按当前用户收紧权限）。"""
    target = Path(path) if path else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    data = asdict(settings)
    extra = data.pop("extra", None) or {}
    data.update(extra)

    with target.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    _restrict_to_current_user(target)
    return target


def _restrict_to_current_user(path: Path) -> None:
    """把文件权限收紧到"只有我自己"。

    写进 DPAPI 的密文别人解不开，但明文兜底模式下这一步很重要。
    """
    if sys.platform != "win32":
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return
    try:
        import subprocess

        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", "{0}:(R,W)".format(os.environ.get("USERNAME", ""))],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def config_exists(path: Optional[Path] = None) -> bool:
    return (Path(path) if path else config_path()).exists()
