---
name: campus-literature
description: 文献检索。基于共享文献引擎（Scopus）搜索学术论文、获取摘要、生成文献包。当用户需要"查文献、搜论文、找相关研究、看摘要"时使用。
metadata:
  openclaw:
    os:
      - windows
      - macos
      - linux
---

# 文献检索

基于共享文献引擎（Scopus API）检索学术论文。数据来自共享底座 `agent/literature/`，凭据走 **campus keyring**（与 agent 项目 `.env` 解耦）。

## 如果你是 AI，请阅读以下内容

### 铁律

- **铁律 1：AI 运行所有脚本**。禁止让用户敲命令。
- **铁律 2：脚本面向 AI**。stdout 输出 JSON，进度写 `runtime/logs/campus.log`，不写 stderr。
- **铁律 3：凭据走 keyring**。`scopus_api_key` / `scopus_inst_token` 经 `creds.py add <key> --value-stdin` 配置，脚本从 keyring 读并经 env 注入子进程。**禁止读根目录 `.env`、禁止硬编码**。缺 key 时主动向用户索取并说明用途。
- **铁律 4：Scopus 配额有限**。免费层 count≤25、start≤5000、429 自动重试。避免无谓浪费。

### 所需凭据

| 凭据 | 用途 | 必需 | 获取 |
|------|------|------|------|
| `scopus_api_key` | 文献检索鉴权 | ✅ | dev.elsevier.com 申请（可清华 CARSI 登录） |
| `scopus_inst_token` | 提升配额/权限 | 可选 | 清华图书馆 Institutional Token |

查询前先跑 `literature.py status`：若 key 未配置，向用户索取并 `creds.py add scopus_api_key --value-stdin`。

### 使用

```
literature.py status                         # 检查 Scopus key 配置
literature.py search -q "<检索式>" --count 10   # 搜索（返回标题/DOI/引用/年份）
literature.py abstract --doi <doi>             # 拿摘要
literature.py full -q "<检索式>" --count 5     # 搜索+摘要完整链路
```

### 检索式示例

- `TITLE(knowledge graph)` — 标题含
- `TITLE-ABS-KEY(llm) AND PUBYEAR > 2023` — 标题/摘要/关键词 + 年份
- `AUTHKEY(transformers)` — 关键词

### 工作流

```
用户: 帮我查一下知识图谱相关的综述
AI:
  1. literature.py status → 未配置则向用户索取 scopus_api_key（creds.py guide 说明用途）
  2. literature.py search -q "TITLE(knowledge graph) AND DOCTYPE(ar)" --count 10
  3. 读 results，按引用量排序汇报（标题 + 年份 + 引用 + DOI）
  4. 用户选中的 → literature.py abstract --doi <doi> 拿摘要
```

### 边界

- Scopus 仅覆盖部分数据库；OpenAlex/arXiv 未接入（共享底座后续扩展）
- 免费层 STANDARD 视图无摘要（需 META_ABS），配额有限
- 清华机构 token（inst_token）提升配额，建议配置

---

## 如果你是用户，请阅读以下内容

对 AI 说："帮我查 XX 相关的文献"。

AI 会搜索学术论文，告诉你标题、年份、被引次数，并可进一步查看摘要。
