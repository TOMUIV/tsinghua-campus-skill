"""browser.py — Chromium CDP 常驻浏览器管理（跨平台）

核心架构：启动一个【常驻】Chromium 进程（带 --remote-debugging-port），
AI 通过 CDP 端口反复连接操纵同一个浏览器实例。
- 浏览器不随脚本退出 → 2FA 验证码不会因"重开浏览器"而失效
- 一律无头模式（--headless=new）：AI 自动流程 / WSL 无显示器 / 全新机器
- profile 复用（runtime/profiles/cdp_profile），CAS cookies 全校通用

端口默认 9222，pid 记录在 runtime/browser/cdp.pid，端口记录在 cdp.port。
"""
import sys
import os
import json
import time
import shutil
import signal
import subprocess
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "shared", "scripts"))
import common

CDP_PORT = 9222
DEBUG_PORT_RANGE = (9200, 9300)


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        return None


def chromium_executable():
    """定位本机 Chromium 可执行文件。Windows/macOS/Linux 都找 Playwright 缓存。"""
    plat = common.detect_platform()
    import glob
    home = str(Path.home())
    if plat == "windows":
        pats = [rf"C:\Users\*\AppData\Local\ms-playwright\chromium-*\chrome-win64\chrome.exe",
                rf"C:\Users\*\AppData\Local\ms-playwright\chromium-*\chrome-win\chrome.exe"]
    elif plat == "macos":
        pats = [f"{home}/Library/Caches/ms-playwright/chromium-*/chrome-mac/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
                f"{home}/Library/Caches/ms-playwright/chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium"]
    else:
        pats = [f"{home}/.cache/ms-playwright/chromium-*/chrome-linux/chrome"]
    for pat in pats:
        cands = sorted(glob.glob(pat))
        if cands:
            return cands[0]
    return None


def _cdp_dir():
    return common.runtime_dir("browser")


def _pid_file():
    return os.path.join(str(_cdp_dir()), "cdp.pid")


def _port_file():
    return os.path.join(str(_cdp_dir()), "cdp.port")


def _save_port(port):
    _cdp_dir().mkdir(parents=True, exist_ok=True)
    with open(_port_file(), "w") as f:
        f.write(str(port))


def _load_port():
    if os.path.exists(_port_file()):
        try:
            return int(open(_port_file()).read().strip())
        except Exception:
            pass
    return CDP_PORT


def _pid_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        # Windows 的 os.kill(pid, 0) 不支持信号 0，会对存活进程抛 OSError(22)
        # → 用 tasklist 精确判断进程是否存在
        try:
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True,
                               text=True, timeout=10)
            return str(pid) in r.stdout
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


def is_running():
    """CDP 浏览器是否已在运行（以调试端口响应为准）。"""
    try:
        import urllib.request
        port = _load_port()
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2)
        return True
    except Exception:
        return False


def _load_pid():
    if os.path.exists(_pid_file()):
        try:
            return int(open(_pid_file()).read().strip())
        except Exception:
            pass
    return None


def _free_port(start):
    import socket
    for port in range(start, DEBUG_PORT_RANGE[1]):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def _kill_port_process(port):
    """杀掉占用指定端口的进程（孤儿残留 Chrome 清理）。"""
    if os.name != "nt":
        return
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess"],
            capture_output=True, text=True, timeout=15)
        for pid in r.stdout.split():
            pid = pid.strip()
            if pid.isdigit():
                subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True)
    except Exception:
        pass


def _clean_broken_cache(profile_path):
    """清理 Chromium profile 中可能导致启动崩溃的损坏缓存目录。

    新机/残留场景下，GPU 缓存（GraphiteDawnCache 等）损坏会使 Chrome 启动即
    崩溃退出（CDP 端口无响应，login 报 ECONNRESET）。删除这些目录可自愈。
    只清理可再生的缓存目录，不碰 cookies/login 数据。
    """
    for name in ("GraphiteDawnCache", "old_GraphiteDawnCache_000",
                 "GPUCache", "GrShaderCache", "ShaderCache",
                 "DawnCache", "DawnGraphiteCache"):
        p = os.path.join(profile_path, name)
        if os.path.isdir(p):
            try:
                shutil.rmtree(p)
                common.log(f"[browser] 已清理损坏缓存: {name}")
            except Exception as e:
                common.log(f"[browser] 清理缓存失败 {name}: {e}")


def _launch_browser(cmd, port, profile_path):
    """启动 Chromium 并等待 CDP 端口就绪。失败则杀进程并返回 None。"""
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000  # CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags, close_fds=True)
    _cdp_dir().mkdir(parents=True, exist_ok=True)
    with open(_pid_file(), "w") as f:
        f.write(str(proc.pid))

    # 等调试端口就绪
    for _ in range(30):
        time.sleep(0.5)
        if _cdp_ready(port):
            return proc
    # 未就绪：杀掉残留进程，避免孤儿 chrome 占用端口
    try:
        if proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            else:
                os.kill(proc.pid, signal.SIGTERM)
    except Exception:
        pass
    return None


def start_cdp(headed=False, profile=None, extra_args=None):
    """启动 CDP 常驻 Chromium（若已运行则复用）。返回 (port, profile_path)。

    若端口有响应但非本进程（孤儿/残留），先尝试杀掉再启动，避免端口冲突。
    extra_args: 额外 Chrome 启动参数（如移除 --disable-blink-features=AutomationControlled
                以解决某些站点 ERR_BLOCKED_BY_CLIENT）。
    """
    # 孤儿清理：端口有响应但 pid 不匹配 → 杀掉残留进程
    if is_running():
        pid = _load_pid()
        if _pid_alive(pid):
            return _load_port(), (profile or str(common.runtime_dir("profiles", "cdp_profile")))
        common.log("[browser] 检测到孤儿浏览器（端口响应但进程不匹配），清理")
        _kill_port_process(_load_port())
        try:
            os.remove(_pid_file())
        except Exception:
            pass

    exe = chromium_executable()
    if not exe:
        raise RuntimeError("Chromium 未找到，先运行 install 模块")

    profile_path = profile or str(common.runtime_dir("profiles", "cdp_profile"))
    os.makedirs(profile_path, exist_ok=True)

    # 选一个当前空闲端口
    port = _free_port(CDP_PORT)
    _save_port(port)

    # 一律无头模式（headless）:
    # - AI 自动流程 / WSL 无显示器 / 全新机器（产品决策：全部 headless，不需人工浏览器）
    # - --headed 参数保留仅向后兼容，但忽略（恒 headless）
    base_flags = ["--no-first-run", "--no-default-browser-check",
                  "--disable-gpu", "--disable-dev-shm-usage",
                  "--no-sandbox"]
    if extra_args is None:
        # 默认保留自动化 flag（常规场景）
        base_flags.append("--disable-blink-features=AutomationControlled")
    else:
        base_flags = [f for f in base_flags if f != "--disable-blink-features=AutomationControlled"]
        base_flags += extra_args
    cmd = [exe, "--headless=new"] + [
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_path}",
    ] + base_flags + ["about:blank"]

    proc = _launch_browser(cmd, port, profile_path)
    if proc is None:
        # 启动失败（常见：profile 损坏 GPU 缓存导致 Chrome 崩溃）→ 清缓存重试一次
        common.log("[browser] CDP 启动失败，清理损坏缓存后重试")
        _clean_broken_cache(profile_path)
        proc = _launch_browser(cmd, port, profile_path)
    if proc is None:
        raise RuntimeError(f"CDP 浏览器启动失败，端口 {port} 未就绪（清缓存重试仍失败）")
    return port, profile_path


def is_port_in_use(port):
    import socket
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _cdp_ready(port):
    try:
        import urllib.request
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2)
        return True
    except Exception:
        return False


def connect_cdp(port=None):
    """通过 CDP 连接常驻浏览器。返回 (pw, browser, context, page)。"""
    sp = _playwright()
    if sp is None:
        raise RuntimeError("playwright 未安装，先运行 install 模块")
    port = port or _load_port()
    pw = sp().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.pages[0] if context.pages else context.new_page()
    return pw, browser, context, page


def stop_cdp():
    """关闭 CDP 浏览器进程。

    双保险：按 pid 文件杀 + 按调试端口杀（pid 文件可能因 Chrome 子进程
    重建/重启而过期，端口监听者才是真实浏览器进程）。
    """
    pid = _load_pid()
    if pid and _pid_alive(pid):
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
    # 端口兜底：pid 文件过期/缺失时，杀端口监听进程
    if os.name == "nt":
        port = _load_port()
        if is_port_in_use(port):
            _kill_port_process(port)
    for f in (_pid_file(), _port_file()):
        try:
            os.remove(f)
        except Exception:
            pass


def ensure_ready():
    """检查 playwright + chromium 是否可用，供 install/selfcheck 调用。"""
    sp = _playwright()
    if sp is None:
        return {"ok": False, "missing": "playwright"}
    exe = chromium_executable()
    if not exe:
        return {"ok": False, "missing": "chromium"}
    return {"ok": True, "chromium": exe, "cdp_running": is_running()}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--start", action="store_true", help="启动 CDP 浏览器")
    ap.add_argument("--stop", action="store_true", help="关闭 CDP 浏览器")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    if args.check:
        r = ensure_ready()
        r["status"] = "ok" if r["ok"] else "error"
        common.output_json(r)
        sys.exit(0 if r["ok"] else 1)
    if args.start:
        port, prof = start_cdp(headed=args.headed)
        common.output_json({"status": "ok", "port": port, "profile": prof})
    elif args.stop:
        stop_cdp()
        common.output_json({"status": "ok", "message": "CDP 浏览器已关闭"})
