"""SQLAlchemy 透明加解密类型(业务代码零改动)

- 双读兼容:无前缀的历史明文原样返回;有前缀时解密(需已解锁);
- 写路径:仅在"加密已启用且已解锁"时加密,否则直通(关闭加密后行为与现状一致);
- process_bind 对比较值同样生效:确定性类型支持 WHERE 等值查询、唯一约束与去重;
- 存储兼容:加密值以带前缀字符串保存,不改变列结构(无需数据库迁移)。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import JSON, String, Text
from sqlalchemy.types import TypeDecorator

from app.services import crypto_runtime, crypto_service


class EncryptedText(TypeDecorator):
    """随机加密字符串(AES-256-GCM);用于内容字段(报告/转录文本等)"""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> Any:
        if value is None or not isinstance(value, str):
            return value
        if not crypto_runtime.should_encrypt_on_write():
            return value
        if crypto_service.is_encrypted(value):
            return value
        return crypto_service.encrypt_text(value, crypto_runtime.get_dek())

    def process_result_value(self, value: Any, dialect) -> Any:
        if isinstance(value, str) and crypto_service.is_encrypted(value):
            return crypto_service.decrypt_text(value, crypto_runtime.get_dek())
        return value


class EncryptedDeterministic(TypeDecorator):
    """确定性加密字符串(AES-256-SIV);用于等值查询字段(姓名/学号)

    注意:仅泄露"等值关系";排序按密文序(仅影响显示顺序,不影响业务)。
    """

    impl = String
    cache_ok = True

    def __init__(self, length: int = 128):
        super().__init__(length=length)

    def process_bind_param(self, value: Any, dialect) -> Any:
        if value is None or not isinstance(value, str):
            return value
        if not crypto_runtime.should_encrypt_on_write():
            return value
        if crypto_service.is_deterministic(value):
            return value
        return crypto_service.encrypt_deterministic(value, crypto_runtime.get_dek())

    def process_result_value(self, value: Any, dialect) -> Any:
        if isinstance(value, str) and crypto_service.is_deterministic(value):
            return crypto_service.decrypt_deterministic(value, crypto_runtime.get_dek())
        return value


class EncryptedJSON(TypeDecorator):
    """加密的 JSON 列(对象整体加密;存储为带前缀字符串的 JSON 值)

    读取兼容三种历史形态:加密字符串 / 明文 JSON 字符串 / 直接对象。
    """

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        if not crypto_runtime.should_encrypt_on_write():
            return value  # 直通:保持既有存储形态(关闭加密时行为不变)
        if isinstance(value, str) and crypto_service.is_encrypted(value):
            return value
        payload = json.dumps(value, ensure_ascii=False)
        return crypto_service.encrypt_text(payload, crypto_runtime.get_dek())

    def process_result_value(self, value: Any, dialect) -> Any:
        if isinstance(value, str):
            if crypto_service.is_encrypted(value):
                value = crypto_service.decrypt_text(value, crypto_runtime.get_dek())
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return value
        return value
