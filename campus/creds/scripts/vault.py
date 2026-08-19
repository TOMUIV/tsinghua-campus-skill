"""vault.py — 统一凭据加密（keyring 优先，Fernet 兜底）

方案: 使用操作系统提供的安全存储 API（keyring 库）加密凭据，
     密钥由系统托管（Windows=凭据管理器/DPAPI、macOS=Keychain、
     Linux=Secret Service），与应用数据分离，安全性很高。
     keyring 不可用时（如 WSL 无桌面会话）自动回退 Fernet 密钥文件。

每个凭据 key 对应系统 keyring 的一个独立条目（account = "campus:<key>"），
避免多个凭据互相覆盖。

存储格式（credentials.json 内只存密文/引用）:
  keyring 后端: "keyring:campus"            → 实际值在系统 keyring（key 对应条目）
  fernet 兜底:  "fernet:<base64 token>"     → Fernet 加密

接口:
  vault_encrypt(key, plaintext) -> str
  vault_decrypt(key, ciphertext) -> str
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "shared", "scripts"))
import common

SERVICE = "campus-skill"

_KEYRING_CACHE = None  # True / False / None(未检测)


def _keyring_ok():
    """检测系统 keyring 是否可用（实际 set/get 探测，避免伪后端）。"""
    global _KEYRING_CACHE
    if _KEYRING_CACHE is not None:
        return _KEYRING_CACHE
    try:
        import keyring
        keyring.set_password(SERVICE, "_probe", "1")
        v = keyring.get_password(SERVICE, "_probe")
        keyring.delete_password(SERVICE, "_probe")
        _KEYRING_CACHE = (v == "1")
    except Exception:
        _KEYRING_CACHE = False
    return _KEYRING_CACHE


def _keyring_account(key):
    return f"campus:{key}" if key else "campus"


def _keyring_set(key, value):
    import keyring
    keyring.set_password(SERVICE, _keyring_account(key), value)


def _keyring_get(key):
    import keyring
    return keyring.get_password(SERVICE, _keyring_account(key))


def _fernet():
    from cryptography.fernet import Fernet

    kf = common.runtime_dir("vault", "vault.key")
    if os.path.exists(kf):
        with open(kf, "rb") as f:
            key = f.read()
    else:
        key = Fernet.generate_key()
        os.makedirs(os.path.dirname(kf), exist_ok=True)
        with open(kf, "wb") as f:
            f.write(key)
        try:
            os.chmod(kf, 0o600)
        except Exception:
            pass
    return Fernet(key)


def vault_encrypt(key, plaintext):
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    if _keyring_ok():
        _keyring_set(key, plaintext.decode("utf-8", errors="replace"))
        return f"keyring:{_keyring_account(key)}"
    token = _fernet().encrypt(plaintext).decode("ascii")
    return "fernet:" + token


def vault_decrypt(key, ciphertext):
    if not ciphertext:
        return ""
    if ciphertext.startswith("keyring:"):
        # 引用里可能带 account（keyring:campus:<key>），兼容旧格式 keyring:campus
        ref = ciphertext[len("keyring:"):]
        if ref.startswith("campus:"):
            # 新版 keyring:campus:<key> 直接用 ref 作为 account
            import keyring
            return keyring.get_password(SERVICE, ref) or ""
        # 旧格式 keyring:campus → 用传入 key 的 account
        v = _keyring_get(key)
        return v or ""
    body = ciphertext[len("fernet:"):] if ciphertext.startswith("fernet:") else ciphertext
    try:
        return _fernet().decrypt(body.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="自测加密/解密往返（多 key 隔离）")
    args = ap.parse_args()
    if args.test:
        ok_all = True
        for k in ["cas_username", "cas_password", "student_id"]:
            probe = f"{k}-值-验证"
            enc = vault_encrypt(k, probe)
            dec = vault_decrypt(k, enc)
            ok = dec == probe
            ok_all &= ok
        # 隔离验证：不同 key 解出不同值
        a = vault_decrypt("cas_username", vault_encrypt("cas_username", "AAA"))
        b = vault_decrypt("cas_password", vault_encrypt("cas_password", "BBB"))
        isolated = (a == "AAA" and b == "BBB" and a != b)
        common.output_json({
            "status": "ok" if (ok_all and isolated) else "error",
            "platform": common.detect_platform(),
            "backend": "keyring" if _keyring_ok() else "fernet-fallback",
            "roundtrip_ok": ok_all,
            "isolated_ok": isolated,
        })
