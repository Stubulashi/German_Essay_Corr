"""加密运行时状态(独立叶子模块,避免模型层与加密服务的循环导入)

- enabled:总开关(控制"新写入是否加密");
- dek:数据加密密钥(仅驻留进程内存,重启后需口令解锁);
- wrap_present:是否已设置口令(存在密钥包裹记录)→ 启动后处于"锁定"态直至解锁;
  注意:"关闭加密但保留密文"模式同样保留 wrap,因此重启后仍需解锁才能读取旧密文。
"""

from __future__ import annotations

import threading


class EncryptionLockedError(RuntimeError):
    """数据已加密但尚未解锁"""


_lock = threading.Lock()
_enabled = False
_wrap_present = False
_dek: bytes | None = None


def configure(*, enabled: bool, wrap_present: bool, dek: bytes | None = None) -> None:
    """启动/状态加载时整体配置"""
    global _enabled, _wrap_present, _dek
    with _lock:
        _enabled = enabled
        _wrap_present = wrap_present
        _dek = dek


def set_dek(dek: bytes | None) -> None:
    global _dek
    with _lock:
        _dek = dek


def set_enabled(enabled: bool) -> None:
    global _enabled
    with _lock:
        _enabled = enabled


def set_wrap_present(present: bool) -> None:
    global _wrap_present
    with _lock:
        _wrap_present = present


def is_enabled() -> bool:
    return _enabled


def is_wrap_present() -> bool:
    return _wrap_present


def is_unlocked() -> bool:
    return _dek is not None


def is_locked() -> bool:
    """已设置口令但未解锁(数据接口应返回 423)"""
    return _wrap_present and _dek is None


def get_dek() -> bytes:
    if _dek is None:
        raise EncryptionLockedError("数据已加密且尚未解锁:请先输入口令解锁")
    return _dek


def should_encrypt_on_write() -> bool:
    """写路径是否加密:启用且已解锁"""
    return _enabled and _dek is not None
