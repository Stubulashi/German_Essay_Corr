"""加密原语(AES-256-GCM / AES-256-SIV / scrypt KDF)——仅使用标准密码学库,禁止自研

- 内容加密:AES-256-GCM(随机 96-bit nonce),存储格式 `encv1:base64(nonce||ct)`,
  带完整性校验(篡改必失败);
- 标识符确定性加密:AES-256-SIV(`encd1:` 前缀;同值同密文,支持 WHERE 等值查询、
  唯一约束与去重;仅泄露"等值关系");
- 子密钥分离:DEK 经 HKDF-SHA256 派生 GCM 与 SIV 两把子密钥(避免同钥多用);
- 口令派生:scrypt(n=2^15, r=8, p=1;参数随包裹元数据保存,支持未来升级)。
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, AESSIV
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

RANDOM_PREFIX = "encv1:"
DETERMINISTIC_PREFIX = "encd1:"
KEY_LEN = 32
SALT_LEN = 16
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1

_GCM_INFO = b"corrector-gcm"
_SIV_INFO = b"corrector-siv"
_WRAP_AAD = b"corrector-wrap"
_DATA_AAD = b"corrector"
_DET_AAD = [b"det"]


class CryptoError(RuntimeError):
    """解密/校验失败(密文被篡改或密钥不匹配)"""


def is_encrypted(value: object) -> bool:
    return isinstance(value, str) and value.startswith(RANDOM_PREFIX)


def is_deterministic(value: object) -> bool:
    return isinstance(value, str) and value.startswith(DETERMINISTIC_PREFIX)


def is_ciphertext(value: object) -> bool:
    return is_encrypted(value) or is_deterministic(value)


def new_dek() -> bytes:
    """生成随机数据加密密钥(DEK)"""
    return os.urandom(KEY_LEN)


def new_salt() -> bytes:
    return os.urandom(SALT_LEN)


def derive_kek(
    password: str,
    salt: bytes,
    *,
    n: int = SCRYPT_N,
    r: int = SCRYPT_R,
    p: int = SCRYPT_P,
) -> bytes:
    """口令 -> 密钥加密密钥(KEK);参数随包裹元数据保存以支持升级"""
    kdf = Scrypt(salt=salt, length=KEY_LEN, n=n, r=r, p=p)
    return kdf.derive(password.encode("utf-8"))


def _subkeys(dek: bytes) -> tuple[bytes, bytes]:
    """DEK -> (GCM 子密钥, SIV 子密钥)"""
    gcm_key = HKDF(algorithm=hashes.SHA256(), length=KEY_LEN, salt=None, info=_GCM_INFO).derive(dek)
    siv_key = HKDF(algorithm=hashes.SHA256(), length=KEY_LEN, salt=None, info=_SIV_INFO).derive(dek)
    return gcm_key, siv_key


def wrap_key(dek: bytes, kek: bytes) -> bytes:
    """用 KEK 包裹 DEK(返回 nonce||ct)"""
    nonce = os.urandom(12)
    return nonce + AESGCM(kek).encrypt(nonce, dek, _WRAP_AAD)


def unwrap_key(blob: bytes, kek: bytes) -> bytes:
    """还原 DEK;口令错误/数据损坏抛 CryptoError"""
    try:
        return AESGCM(kek).decrypt(blob[:12], blob[12:], _WRAP_AAD)
    except InvalidTag as e:
        raise CryptoError("口令错误或密钥包裹数据已损坏") from e


def encrypt_text(plaintext: str, dek: bytes) -> str:
    """随机加密(内容字段)"""
    gcm_key, _ = _subkeys(dek)
    nonce = os.urandom(12)
    ct = AESGCM(gcm_key).encrypt(nonce, plaintext.encode("utf-8"), _DATA_AAD)
    return RANDOM_PREFIX + base64.b64encode(nonce + ct).decode("ascii")


def decrypt_text(token: str, dek: bytes) -> str:
    """解密随机加密值;失败抛 CryptoError"""
    gcm_key, _ = _subkeys(dek)
    try:
        raw = base64.b64decode(token[len(RANDOM_PREFIX):])
        return AESGCM(gcm_key).decrypt(raw[:12], raw[12:], _DATA_AAD).decode("utf-8")
    except (InvalidTag, ValueError, UnicodeDecodeError) as e:
        raise CryptoError("密文解密失败(数据可能被篡改,或密钥不匹配)") from e


def encrypt_deterministic(plaintext: str, dek: bytes) -> str:
    """确定性加密(等值查询字段;同值同密文)"""
    _, siv_key = _subkeys(dek)
    ct = AESSIV(siv_key).encrypt(plaintext.encode("utf-8"), _DET_AAD)
    return DETERMINISTIC_PREFIX + base64.b64encode(ct).decode("ascii")


def decrypt_deterministic(token: str, dek: bytes) -> str:
    """解密确定性加密值;失败抛 CryptoError"""
    _, siv_key = _subkeys(dek)
    try:
        raw = base64.b64decode(token[len(DETERMINISTIC_PREFIX):])
        return AESSIV(siv_key).decrypt(raw, _DET_AAD).decode("utf-8")
    except (InvalidTag, ValueError, UnicodeDecodeError) as e:
        raise CryptoError("密文解密失败(数据可能被篡改,或密钥不匹配)") from e
