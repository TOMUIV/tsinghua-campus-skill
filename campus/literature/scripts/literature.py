"""literature.py — 文献检索子 SKILL 统一入口

封装共享文献引擎（Scopus 等），供 AI 和主 SKILL 调用。JSON 输出。
凭据走 campus keyring（creds.py add scopus_api_key / scopus_inst_token），
与 agent 项目 .env 完全解耦——通过 subprocess env 注入 scopus_client。

用法:
  literature.py status                                # 凭据状态 + 各 key 用途说明
  literature.py search --query <检索式> [--count N] [--date 年份]
  literature.py abstract --id <scopus_id> | --doi <doi>
  literature.py full --query <检索式> [--count N]   # 搜索+摘要

本子 SKILL 需要的凭据（用户提供）:
  - scopus_api_key:     Scopus API Key（dev.elsevier.com 申请，文献检索必需）
  - scopus_inst_token:  Scopus 机构 Token（可选，提升配额，清华图书馆可申请）
"""
import sys
import os
import json
import subprocess

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "shared", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "creds", "scripts"))
import common
import creds
import vault

# 共享底座路径（agent/literature）
SCOPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "agent", "literature", "scopus_client.py")


def _get_key(key):
    """从 campus keyring 读凭据明文（不存在返回空串）。"""
    stored = creds._load_creds()
    raw = stored.get(key, "")
    if not raw:
        return ""
    try:
        return vault.vault_decrypt(key, raw)
    except Exception:
        return ""


def _run_scopus(args_list):
    """调用 scopus_client（--quiet 静默，无 stderr 噪音），返回 (ok, 结果列表)。

    key 从 keyring 读，经 env 注入子进程（不写命令行、不依赖 agent .env）。
    """
    api_key = _get_key("scopus_api_key")
    if not api_key:
        common.output_json({"status": "error", "error": "missing_cred",
                            "message": "缺少 Scopus API Key。请让用户提供 scopus_api_key（dev.elsevier.com 申请），"
                                       "用 creds.py add scopus_api_key --value-stdin 配置。"})
        sys.exit(1)
    env = os.environ.copy()
    env["SCOPUS_API_KEY"] = api_key
    inst = _get_key("scopus_inst_token")
    if inst:
        env["SCOPUS_INST_TOKEN"] = inst

    cmd = [sys.executable, SCOPUS, "--quiet"] + args_list
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    lines = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                lines.append(json.loads(line))
            except Exception:
                continue
    return r.returncode == 0, lines


def _summarize(entries, limit=10):
    """从 search_result 行提取简洁条目。"""
    out = []
    for e in entries:
        title = e.get("dc:title") or e.get("dc:title", "")
        if isinstance(title, list):
            title = title[0]
        out.append({
            "title": title,
            "id": e.get("dc:identifier", ""),
            "doi": e.get("prism:doi", ""),
            "year": e.get("prism:coverDate", "")[:4],
            "cited": e.get("citedby-count", ""),
            "authors": e.get("dc:creator", ""),
        })
    return out[:limit]


def cmd_search(query, count=10, date=None):
    ok, lines = _run_scopus(["search", "-q", query, "--count", str(count)] + (["--date", date] if date else []))
    if not ok:
        common.output_json({"status": "error", "message": "Scopus 搜索失败（检查 scopus_api_key 是否有效，用 creds.py status 查看）"})
        sys.exit(1)
    meta = next((l for l in lines if l.get("phase") == "search_meta"), {})
    results = [l["data"] for l in lines if l.get("phase") == "search_result"]
    common.output_json({
        "status": "ok",
        "total": meta.get("data", {}).get("total", len(results)),
        "results": _summarize(results),
    })


def cmd_abstract(doi=None, scopus_id=None):
    args = ["abstract"]
    if doi:
        args += ["--doi", doi]
    elif scopus_id:
        args += ["--id", scopus_id]
    else:
        common.output_json({"status": "error", "message": "需要 --doi 或 --id"})
        sys.exit(1)
    ok, lines = _run_scopus(args)
    if not ok:
        common.output_json({"status": "error", "message": "Scopus 摘要获取失败"})
        sys.exit(1)
    abs_data = next((l.get("data", {}) for l in lines if l.get("phase") == "abstract_result"), {})
    ar = abs_data.get("abstracts-retrieval-response", abs_data)
    common.output_json({
        "status": "ok",
        "title": ar.get("coredata", {}).get("dc:title", ""),
        "doi": ar.get("coredata", {}).get("prism:doi", ""),
        "abstract": (ar.get("coredata", {}).get("dc:description") or ar.get("abstract", ""))[:2000],
        "authors": ar.get("coredata", {}).get("dc:creator", ""),
    })


def cmd_full(query, count=5, date=None):
    args = ["full", "-q", query, "--count", str(count)] + (["--date", date] if date else [])
    ok, lines = _run_scopus(args)
    if not ok:
        common.output_json({"status": "error", "message": "Scopus 完整链路失败"})
        sys.exit(1)
    items = [l["data"] for l in lines if l.get("phase") == "full_abstract_item"]
    out = []
    for it in items:
        coredata = it.get("coredata", {})
        out.append({
            "title": coredata.get("dc:title", ""),
            "doi": coredata.get("prism:doi", ""),
            "abstract": (coredata.get("dc:description") or "")[:1500],
            "authors": coredata.get("dc:creator", ""),
        })
    common.output_json({"status": "ok", "results": out})


def cmd_status():
    """凭据状态 + 各 key 用途说明（面向用户/AI 透明展示本子技能需要哪些凭据）。"""
    key = _get_key("scopus_api_key")
    inst = _get_key("scopus_inst_token")
    common.output_json({
        "status": "ok" if key else "error",
        "scopus_key_configured": bool(key),
        "inst_token_configured": bool(inst),
        "required_creds": [
            {
                "key": "scopus_api_key",
                "label": "Scopus API Key",
                "purpose": "文献检索/摘要/引用的 API 鉴权（必需）",
                "how_to_get": "dev.elsevier.com 注册申请（可走清华 CARSI 机构登录），免费层每日配额",
                "configured": bool(key),
            },
            {
                "key": "scopus_inst_token",
                "label": "Scopus 机构 Token",
                "purpose": "提升配额与数据权限（可选，无则降级免费层）",
                "how_to_get": "清华订阅的 Institutional Token，可向图书馆申请",
                "configured": bool(inst),
            },
        ],
        "guide": "creds.py guide scopus_api_key" if not key else None,
    })


def main():
    import argparse
    ap = argparse.ArgumentParser(description="文献检索统一入口")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("search")
    p.add_argument("-q", "--query", required=True)
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--date")
    p = sub.add_parser("abstract")
    p.add_argument("--doi")
    p.add_argument("--id")
    p = sub.add_parser("full")
    p.add_argument("-q", "--query", required=True)
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--date")
    sub.add_parser("status")
    args = ap.parse_args()

    if args.cmd == "search":
        cmd_search(args.query, args.count, args.date)
    elif args.cmd == "abstract":
        cmd_abstract(args.doi, args.id)
    elif args.cmd == "full":
        cmd_full(args.query, args.count, args.date)
    elif args.cmd == "status":
        cmd_status()


if __name__ == "__main__":
    main()
