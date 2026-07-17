#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_dashboard.py
────────────────────────────────────────────────────────────────────────
质检爬坡准确率 · 看板一键刷新 + 自动发布脚本
────────────────────────────────────────────────────────────────────────

【这个脚本一次帮你做完 3 件事】
  1) 连接飞书，读取指定 Wiki 页面里的「质检（爬坡）准确率」子表格最新数据
  2) 用内置的看板模板，重新生成 index.html（保存到你的 GitHub 仓库目录）
  3) 自动 git add / commit / push，推到 GitHub —— GitHub Pages 会自动更新
     固定访问地址：https://dawangw178-commits.github.io/quality-dashboard/

────────────────────────────────────────────────────────────────────────
【使用前的一次性准备】
────────────────────────────────────────────────────────────────────────

■ A. 安装 Python 依赖（终端粘贴一次即可）
      pip3 install requests

■ B. 准备飞书 App ID / App Secret + 用户授权（3~5 分钟，只做一次）
  说明：本脚本现在使用「用户身份（user_access_token）」读取数据。
  你仍然需要一个自建应用，但真正访问 Wiki / Sheet 的是你本人授权后的身份。

  1. 浏览器打开：https://open.larksuite.com/app
     用你的飞书账号（wangzhengqian.nigs）登录
  2. 点右上角「创建企业自建应用」，随便起个名（如「看板刷新」），提交
  3. 进入应用后按顺序配置：
     ┌─ 左侧「凭证与基础信息」→ 记下 App ID / App Secret
     ├─ 左侧「安全设置」→ 添加重定向 URL：
    │    · http://127.0.0.1:8080
     ├─ 左侧「权限管理」→ 添加并开启：
     │    · 获取用户 user ID        (auth:user.id:read)
     │    · 查看知识空间的节点信息   (wiki:node:read)
     │    · 查看电子表格             (sheets:spreadsheet:read)
     └─ 左侧「版本管理与发布」→ 创建版本 → 提交发布（管理员通过即可）
  4. 确认你自己的飞书账号本来就有这个 Wiki / Sheet 的访问权限
     （因为现在走的是“你的用户身份”，不是“应用协作者”）
  5. 把下面 APP_ID / APP_SECRET 引号里填上你自己的值并保存

■ C. 让 git push 能免密推送（只做一次）
  本仓库用的是 https 地址，第一次推送需要 GitHub 授权。二选一：

  · 方式①（推荐）：用「个人访问令牌 PAT」
      1) 打开 https://github.com/settings/tokens 生成一个 classic token，
         勾选 repo 权限，复制保存（形如 ghp_xxxxxxxx）
      2) 终端执行一次（Mac 会把它存进钥匙串，之后自动记住）：
           cd ~/Documents/code/quality-dashboard
           git push
         弹出账号时填 GitHub 用户名 dawangw178-commits，
         密码那一栏粘贴刚才的 PAT（不是登录密码！）
  · 方式②：先手动成功 push 一次，Mac 钥匙串会记住凭证，之后本脚本就能自动推。

────────────────────────────────────────────────────────────────────────
【日常使用】准备好后，以后每次只要双击或在终端运行：
      python3 refresh_dashboard.py
  脚本会自动拉数据 → 生成 index.html → 推送到 GitHub。稍等 1~2 分钟，
  刷新 https://dawangw178-commits.github.io/quality-dashboard/ 就是最新看板。

【可选】不想把密钥写死在代码里，也可以用环境变量：
      export FEISHU_APP_ID=cli_xxxxxxxxx
      export FEISHU_APP_SECRET=xxxxxxxxxxxxxx
      # 仓库目录如不是默认位置，也可覆盖：
      export REPO_DIR=~/Documents/code/quality-dashboard
"""

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from datetime import datetime
from statistics import mean
from urllib.parse import parse_qs, urlencode, urlparse
from typing import Optional

# ═══════════════════ 第 1 步：填写飞书凭证 ═══════════════════
# 也可改用环境变量：export FEISHU_APP_ID=xxx / export FEISHU_APP_SECRET=xxx
# 注意：本脚本改为“用户身份（user_access_token）”访问飞书。
# 你需要在飞书开放平台完成以下配置：
#   1) 安全设置 → 添加重定向 URL： http://127.0.0.1:8080
#   2) 权限管理 → 开启 auth:user.id:read（用户身份）
#   3) 同时确保 wiki:node:read、sheets:spreadsheet:read 已开通并发布版本
APP_ID     = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")

# ═══════════════════ 第 2 步：指定要读取的表 ═══════════════════
# 直接使用 Wiki token（就是 /wiki/ 后面那一串），API 调用不需要 tenant 域名
WIKI_TOKEN = "Gx6DwNchOi3kIMkH9yGcB9ZFnTb"
SHEET_ID   = "Gn4y0M"      # 子表 sheet_id（Wiki URL 里 ?sheet= 后面那一串）

# ═══════════════════ 第 3 步：GitHub 仓库本地目录 ═══════════════════
# 生成的 index.html 会写到这里，并从这里 git push。可用环境变量 REPO_DIR 覆盖。
REPO_DIR   = Path(os.environ.get("REPO_DIR", "~/Documents/code/quality-dashboard")).expanduser()
INDEX_HTML = REPO_DIR / "index.html"                     # ← 生成的看板文件
OUT_JSON   = REPO_DIR / "quality_raw_extracted.json"     # ← 原始数据（留档，方便排查）
PAGES_URL  = "https://wwgit-cell.github.io/quality-dashboard/"

FEISHU_HOST = "https://open.larksuite.com"   # 字节内网飞书开放平台域名
OOB_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
LOCAL_CALLBACK_REDIRECT_URI = "http://127.0.0.1:8080"
REDIRECT_URI = LOCAL_CALLBACK_REDIRECT_URI
REDIRECT_URI_ALTERNATIVES = [
    "http://127.0.0.1:8080",
    "http://127.0.0.1:8080/",
    "http://localhost:8080",
    "http://localhost:8080/",
    "https://127.0.0.1:8080",
    "https://localhost:8080",
]
AUTH_MODE = os.environ.get("FEISHU_AUTH_MODE", "user")
TOKEN_FILE = Path(__file__).resolve().parent / ".feishu_token.json"
OAUTH_SCOPE = "auth:user.id:read wiki:node:read sheets:spreadsheet:read"

# ─── 依赖检查 ────────────────────────────────────────────────
try:
    import requests
except ImportError:
    print("❌ 缺少依赖 requests，请在终端先执行一次：")
    print("     pip3 install requests")
    sys.exit(1)


# ═══════════════════ 工具函数 ═══════════════════
def die(msg: str, extra: str = ""):
    """遇到问题时统一打印中文错误提示并退出。"""
    print(f"\n❌ {msg}")
    if extra:
        print(f"   详情：{extra}")
    sys.exit(1)


def col_letter(n: int) -> str:
    """把列序号（1 起）转成 A / B / … / AA 这种 Excel 列字母。"""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def parse_percent(v):
    """把 '87.00%' / '80.5' / '-' / '' 之类的值统一变成 float 或 None。"""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "-", "--"):
        return None
    s = s.replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


# ═══════════════════ 飞书 API 调用 ═══════════════════
def ensure_app_configured():
    """检查 App 配置是否填写完整。"""
    if not APP_ID or "填你的" in APP_ID or not APP_SECRET or "填你的" in APP_SECRET:
        die("请先在脚本顶部填写 APP_ID / APP_SECRET",
            "见脚本头部注释 B 部分，3 分钟就能搞定")



def now_ts() -> int:
    return int(datetime.now().timestamp())



def load_token_file() -> Optional[dict]:
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        die("读取 .feishu_token.json 失败", f"{TOKEN_FILE}: {e}")



def save_token_file(token_data: dict):
    payload = dict(token_data)
    payload["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    TOKEN_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")



def access_token_valid(token_data: dict) -> bool:
    expires_at = int(token_data.get("access_token_expires_at") or 0)
    return bool(token_data.get("access_token")) and now_ts() < max(0, expires_at - 60)



def refresh_token_valid(token_data: dict) -> bool:
    expires_at = int(token_data.get("refresh_token_expires_at") or 0)
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        return False
    if expires_at == 0:
        return True
    return now_ts() < max(0, expires_at - 60)



def build_oauth_url(state: str, redirect_uri: str) -> str:
    params = {
        "app_id": APP_ID,
        "redirect_uri": redirect_uri,
        "scope": OAUTH_SCOPE,
        "state": state,
    }
    return f"{FEISHU_HOST}/open-apis/authen/v1/authorize?{urlencode(params)}"



def exchange_code_for_tokens(code: str, redirect_uri: str) -> dict:
    endpoints = [
        f"{FEISHU_HOST}/open-apis/authen/v1/oidc/access_token",
        f"{FEISHU_HOST}/open-apis/authen/v1/access_token",
        f"{FEISHU_HOST}/open-apis/open-apis/authen/v1/oidc/access_token",
        "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token",
        "https://open.feishu.cn/open-apis/authen/v1/access_token",
    ]
    candidates = [redirect_uri] + [u for u in REDIRECT_URI_ALTERNATIVES if u != redirect_uri]
    
    last_error = None
    for endpoint in endpoints:
        for i, candidate in enumerate(candidates):
            r = requests.post(
                endpoint,
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                    "app_id": APP_ID,
                    "app_secret": APP_SECRET,
                    "redirect_uri": candidate,
                },
                timeout=15,
            )
            data = r.json()
            if data.get("code") == 0:
                print(f"   ✔ 使用 endpoint: {endpoint}, redirect_uri: {candidate}")
                return normalize_token_payload(data.get("data") or {}, candidate)
            last_error = f"{data.get('code')} {data.get('msg')}"
            print(f"   ℹ️ 尝试 endpoint={endpoint.split('/')[-1]}, uri={candidate} → {data.get('code')} {data.get('msg')}")
    
    print(f"\n   🔍 调试信息：")
    print(f"      - FEISHU_HOST: {FEISHU_HOST}")
    print(f"      - APP_ID: {APP_ID}")
    print(f"      - 授权时使用的 redirect_uri: {redirect_uri}")
    print(f"      - 请确认在 {FEISHU_HOST}/app 中配置的重定向 URL 与上述一致")
    die("用授权 code 换取 user_access_token 失败",
        f"{last_error} — 请在飞书开放平台安全设置中检查重定向 URL 是否包含以下任一：\n"
        f"   {', '.join(REDIRECT_URI_ALTERNATIVES)}")



def refresh_user_token(refresh_token: str) -> dict:
    r = requests.post(
        f"{FEISHU_HOST}/open-apis/authen/v1/oidc/refresh_access_token",
        json={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "app_id": APP_ID,
            "app_secret": APP_SECRET,
        },
        timeout=15,
    )
    data = r.json()
    if data.get("code") != 0:
        die("refresh_token 刷新失败，请重新授权",
            f"{data.get('code')} {data.get('msg')}")
    return normalize_token_payload(data.get("data") or {})



def normalize_token_payload(data: dict, redirect_uri: str) -> dict:
    access_token = data.get("access_token") or data.get("user_access_token")
    refresh_token = data.get("refresh_token")
    access_expires_in = int(data.get("expires_in") or data.get("access_token_expires_in") or 0)
    refresh_expires_in = int(data.get("refresh_token_expires_in") or 0)
    if not access_token or not refresh_token:
        die("飞书返回的 token 信息不完整", str(data))
    token_data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "access_token_expires_in": access_expires_in,
        "refresh_token_expires_in": refresh_expires_in,
        "access_token_expires_at": now_ts() + access_expires_in,
        "refresh_token_expires_at": now_ts() + refresh_expires_in,
        "redirect_uri": redirect_uri,
        "scope": OAUTH_SCOPE,
    }
    if data.get("token_type"):
        token_data["token_type"] = data["token_type"]
    return token_data



def wait_for_oauth_code_via_local_server(timeout: int = 180) -> str:
    """启动本地临时 HTTP 服务，自动接收 OAuth redirect 里的 code。"""
    code_box = {"code": None, "error": None}
    done = threading.Event()

    class OAuthCallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path != "/":
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("Not Found".encode("utf-8"))
                return

            if query.get("error"):
                code_box["error"] = query.get("error_description", query.get("error", [""]))[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write("<html><body><h3>授权未完成</h3><p>你可以关闭这个页面，回到终端查看错误信息。</p></body></html>".encode("utf-8"))
                done.set()
                return

            code = query.get("code", [None])[0]
            if code:
                code_box["code"] = code
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write("<html><body><h3>授权成功</h3><p>已自动获取授权 code，你可以关闭这个页面并返回终端。</p></body></html>".encode("utf-8"))
                done.set()
                return

            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<html><body><h3>缺少 code 参数</h3><p>请返回终端重新执行。</p></body></html>".encode("utf-8"))
            done.set()

        def log_message(self, format, *args):
            return

    try:
        server = HTTPServer(("127.0.0.1", 8080), OAuthCallbackHandler)
    except OSError as e:
        die("无法启动本地回调服务 http://127.0.0.1:8080",
            f"端口可能被占用：{e}")

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not done.wait(timeout=timeout):
            die("等待浏览器授权超时", "请重新运行脚本，并在浏览器完成授权")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    if code_box["error"]:
        die("飞书授权失败", code_box["error"])
    if not code_box["code"]:
        die("没有捕获到授权 code", "请确认飞书开放平台已添加 http://127.0.0.1:8080 为重定向 URL")
    return code_box["code"]



def authorize_user_token() -> dict:
    state = secrets.token_urlsafe(16)
    print("\n🔐 首次需要飞书授权（用户身份）")
    print("   即将在本地 8080 端口监听回调，授权成功后脚本自动拿到 code。")

    local_auth_url = build_oauth_url(state, LOCAL_CALLBACK_REDIRECT_URI)
    print("\n   请在浏览器中打开以下链接完成授权（如果没有自动弹出）：")
    print(local_auth_url)
    try:
        webbrowser.open(local_auth_url)
        print("   （已尝试帮你自动打开浏览器）")
    except Exception:
        pass
    code = wait_for_oauth_code_via_local_server()
    token_data = exchange_code_for_tokens(code, LOCAL_CALLBACK_REDIRECT_URI)
    save_token_file(token_data)
    print(f"   ✔ 已保存 token 文件：{TOKEN_FILE}")
    return token_data



def get_tenant_access_token() -> str:
    """使用应用授权模式获取 tenant_access_token，无需用户交互。"""
    r = requests.post(
        f"{FEISHU_HOST}/open-apis/auth/v3/tenant_access_token/internal",
        json={
            "app_id": APP_ID,
            "app_secret": APP_SECRET,
        },
        timeout=15,
    )
    try:
        data = r.json()
    except Exception as e:
        die("获取 tenant_access_token 失败",
            f"无法解析响应：{e}\n原始响应：{r.text[:500]}")
    if data.get("code") != 0:
        die("获取 tenant_access_token 失败",
            f"{data.get('code')} {data.get('msg')}")
    token = data.get("data", {}).get("tenant_access_token") or data.get("tenant_access_token")
    if not token:
        die("获取 tenant_access_token 失败",
            f"响应数据不完整：{json.dumps(data, ensure_ascii=False)}")
    print(f"   ✔ 获取 tenant_access_token 成功")
    return token


def get_token() -> str:
    """获取访问 token：根据 AUTH_MODE 选择用户授权或应用授权模式。"""
    ensure_app_configured()
    
    if AUTH_MODE == "tenant":
        print("   ℹ️ 使用应用授权模式（tenant_access_token）")
        return get_tenant_access_token()
    
    print("   ℹ️ 使用用户授权模式（user_access_token）")
    token_data = load_token_file()
    if token_data and access_token_valid(token_data):
        return token_data["access_token"]
    if token_data and refresh_token_valid(token_data):
        print("   ℹ️ access_token 已过期，正在用 refresh_token 自动刷新 …")
        token_data = refresh_user_token(token_data["refresh_token"])
        save_token_file(token_data)
        return token_data["access_token"]
    if token_data:
        print("   ℹ️ 本地 refresh_token 也已过期，需要重新授权 …")
    token_data = authorize_user_token()
    return token_data["access_token"]


def resolve_wiki(token: str) -> str:
    """第 2 步：把 Wiki 节点解析成真正的电子表格 token。"""
    r = requests.get(
        f"{FEISHU_HOST}/open-apis/wiki/v2/spaces/get_node",
        params={"token": WIKI_TOKEN},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    data = r.json()
    if data.get("code") != 0:
        die("解析 Wiki 节点失败",
            f"{data.get('code')} {data.get('msg')} — 常见原因：你当前授权账号无该 Wiki 访问权限，或应用未开通对应权限")
    node = data["data"]["node"]
    if node.get("obj_type") != "sheet":
        die("这个 Wiki 节点不是电子表格", f"obj_type={node.get('obj_type')}")
    return node["obj_token"]


def get_sheet_meta(token: str, spreadsheet_token: str) -> dict:
    """第 3 步：拿子表的元信息，主要是行列数。"""
    r = requests.get(
        f"{FEISHU_HOST}/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    data = r.json()
    if data.get("code") != 0:
        die("读取表格结构失败", f"{data.get('code')} {data.get('msg')}")
    for s in data["data"]["sheets"]:
        if s["sheet_id"] == SHEET_ID:
            return s
    die("在电子表格里没找到指定子表", f"sheet_id={SHEET_ID}")


def get_values(token: str, spreadsheet_token: str, a1_range: str) -> list:
    """第 4 步：真正把单元格值读出来（二维数组）。"""
    r = requests.get(
        f"{FEISHU_HOST}/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{a1_range}",
        params={
            "valueRenderOption": "ToString",
            "dateTimeRenderOption": "FormattedString",
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    data = r.json()
    if data.get("code") != 0:
        die("读取表格数据失败", f"{data.get('code')} {data.get('msg')}")
    return data["data"]["valueRange"]["values"] or []


# ═══════════════════ 数据整理 ═══════════════════
def build_raw_payload(rows: list) -> dict:
    """把飞书返回的二维数组整理成 quality_raw_extracted.json 结构。"""
    if not rows:
        die("表格为空，无法生成 JSON")

    headers = [
        (str(h).replace("\ufeff", "").strip() if h is not None else "")
        for h in rows[0]
    ]
    body = rows[1:]

    identity_headers = ["姓名", "是否能独立质检", "队列", "方向", "趋势"]
    date_headers = [h for h in headers
                    if h and re.match(r"^\d{4}[/\-]\d{1,2}[/\-]\d{1,2}$", h)]

    def cell(r, name):
        if name not in headers:
            return ""
        i = headers.index(name)
        v = r[i] if i < len(r) else ""
        return "" if v is None else str(v).strip()

    # 找到「至少有一行有数据」的列，作为非空列
    non_empty = []
    for i, h in enumerate(headers):
        if not h:
            continue
        any_val = any(
            str((r[i] if i < len(r) else "")).strip() not in ("", "-", "--")
            for r in body
        )
        if any_val:
            non_empty.append(h)

    other_headers   = [h for h in non_empty if h not in identity_headers and h not in date_headers]
    visible_headers = [h for h in identity_headers if h in non_empty] + date_headers + other_headers

    people = []
    for idx, r in enumerate(body, start=1):
        name = cell(r, "姓名")
        if not name:
            continue
        dates = {h: cell(r, h) for h in date_headers}
        numeric = [x for x in (parse_percent(dates[h]) for h in date_headers) if x is not None]
        mean_val = round(mean(numeric), 2) if numeric else None
        people.append({
            "row_index": idx,
            "name": name,
            "raw": {h: cell(r, h) for h in headers},
            "visible": {h: cell(r, h) for h in visible_headers},
            "dates": dates,
            "mean_accuracy": mean_val,
            "valid_score_count": len(numeric),
        })

    return {
        "source_file": "feishu_live_pull",
        "row_count": len(people),
        "column_count": len(headers),
        "headers": headers,
        "non_empty_headers": non_empty,
        "visible_headers": visible_headers,
        "identity_headers": [h for h in identity_headers if h in non_empty],
        "date_headers": date_headers,
        "other_headers": other_headers,
        "rows": people,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def build_dashboard_payload(raw: dict) -> dict:
    """把 raw JSON 转换成看板 HTML 里 `const DATA = {...}` 直接可用的结构。"""
    date_headers = raw["date_headers"]

    def to_short(h):
        m = re.match(r"^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})$", h)
        return f"{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else h

    def normalize(h):
        m = re.match(r"^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})$", h)
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else h

    short_dates = [to_short(h) for h in date_headers]

    people = []
    all_scores = []
    for r in raw["rows"]:
        scores = [parse_percent(r["dates"].get(h)) for h in date_headers]
        valid  = [x for x in scores if x is not None]
        latest = next((v for v in reversed(scores) if v is not None), None)
        mean_val = round(sum(valid) / len(valid), 2) if valid else None
        all_scores.extend(valid)
        people.append({
            "name": r["name"],
            "queue": r["raw"].get("队列", "").strip(),
            "direction": r["raw"].get("方向", "").strip(),
            "independent": r["raw"].get("是否能独立质检", "").strip(),
            "scores": scores,
            "valid_count": len(valid),
            "mean": mean_val,
            "latest": latest,
            "lowest": min(valid) if valid else None,
            "highest": max(valid) if valid else None,
        })
    overall = round(sum(all_scores) / len(all_scores), 2) if all_scores else 0
    risk_count = sum(1 for p in people if p["mean"] is not None and p["mean"] < 90)

    return {
        "shortDates": short_dates,
        "dateHeaders": date_headers,
        "people": people,
        "overallMean": overall,
        "riskCount": risk_count,
        "dateRangeText": (f"{normalize(date_headers[0])} 至 {normalize(date_headers[-1])}"
                           if date_headers else "-"),
        "firstDate": normalize(date_headers[0]) if date_headers else "",
        "lastDate":  normalize(date_headers[-1]) if date_headers else "",
        "dateCount": len(date_headers),
        "totalPeople": len(people),
        "queues":     sorted({p["queue"] for p in people if p["queue"]}),
        "directions": sorted({p["direction"] for p in people if p["direction"]}),
        "generatedTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def write_index_html(dashboard_payload: dict):
    """用内置模板 + 最新数据，重新生成 index.html。"""
    payload_json = json.dumps(dashboard_payload, ensure_ascii=False)
    if "__DASHBOARD_DATA__" not in HTML_TEMPLATE:
        die("内置模板缺少数据占位符 __DASHBOARD_DATA__", "脚本被改动过？")
    html = HTML_TEMPLATE.replace("__DASHBOARD_DATA__", payload_json)
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_HTML.write_text(html, encoding="utf-8")


# ═══════════════════ Git 自动推送 ═══════════════════
def run_git(args: list) -> subprocess.CompletedProcess:
    """在仓库目录里执行一条 git 命令，返回结果（不抛异常，交给调用方判断）。"""
    return subprocess.run(
        ["git", "-C", str(REPO_DIR), *args],
        capture_output=True, text=True,
    )


def git_publish():
    """git add / commit / push，把最新看板推到 GitHub。"""
    # 0) 确认是个 git 仓库
    if not (REPO_DIR / ".git").exists():
        die(f"{REPO_DIR} 不是一个 git 仓库",
            "请确认已经 clone：git clone "
            "https://github.com/dawangw178-commits/quality-dashboard.git")

    # 1) 当前分支
    br = run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    branch = br.stdout.strip() or "main"

    # 2) 暂存生成的文件
    add = run_git(["add", "index.html", "quality_raw_extracted.json"])
    if add.returncode != 0:
        die("git add 失败", add.stderr.strip())

    # 3) 有变化才提交（避免空提交报错）
    if run_git(["diff", "--cached", "--quiet"]).returncode == 0:
        print("   ℹ️  数据没有变化，无需提交（跳过 commit / push）")
        return

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"看板数据自动刷新 {stamp}"
    commit = run_git(["commit", "-m", msg])
    if commit.returncode != 0:
        die("git commit 失败", commit.stderr.strip() or commit.stdout.strip())
    print(f"   ✔ 已提交：{msg}")

    # 4) 推送
    print(f"   ⏫ 正在推送到 GitHub（分支 {branch}）…")
    push = run_git(["push", "origin", branch])
    if push.returncode != 0:
        detail = (push.stderr.strip() or push.stdout.strip())
        die("git push 失败（多半是没配置好 GitHub 授权）",
            detail + "\n   → 解决办法见脚本头部注释 C 部分：先手动 "
                     "`cd ~/Documents/code/quality-dashboard && git push` "
                     "成功一次，输入 GitHub 用户名 + 个人访问令牌 PAT 即可。")
    print("   ✔ 推送成功")


# ═══════════════════ 主流程 ═══════════════════
def main():
    print("=" * 60)
    print("🍳  质检爬坡准确率看板 · 刷新数据并自动发布到 GitHub")
    print("=" * 60)

    print("[1/7] 获取飞书访问凭证 …")
    token = get_token()

    print("[2/7] 解析 Wiki 页面 → 电子表格 token …")
    spreadsheet_token = resolve_wiki(token)

    print("[3/7] 读取子表元信息（行/列数）…")
    meta = get_sheet_meta(token, spreadsheet_token)
    grid = meta.get("grid_properties", {})
    row_count = grid.get("row_count") or meta.get("row_count") or 200
    col_count = grid.get("column_count") or meta.get("column_count") or 26
    a1 = f"{SHEET_ID}!A1:{col_letter(col_count)}{row_count}"

    print(f"[4/7] 拉取单元格数据（范围 {a1}）…")
    values = get_values(token, spreadsheet_token, a1)

    print("[5/7] 解析并保存 quality_raw_extracted.json …")
    raw_payload = build_raw_payload(values)
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(raw_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("[6/7] 用内置模板重新生成 index.html …")
    dashboard_payload = build_dashboard_payload(raw_payload)
    write_index_html(dashboard_payload)

    print("[7/7] 提交并推送到 GitHub …")
    git_publish()

    print()
    print(f"✅ 全部完成！本次拉取 {raw_payload['row_count']} 位人员，"
          f"{len(raw_payload['date_headers'])} 个日期。")
    print(f"   · 生成文件：{INDEX_HTML}")
    print(f"   · 在线看板（约 1~2 分钟后更新）：{PAGES_URL}")
    print("   打开上面的链接就能看到最新看板 🎉")


# ═══════════════════ 内置看板模板（构建时注入，勿手动改动占位符）═══════════════════
HTML_TEMPLATE = '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n  <meta charset="UTF-8" />\n  <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n  <title>质检爬坡准确率 · 人员趋势看板</title>\n  <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>\n  <style>\n    :root {\n      --chart-h: 150px;\n      --bg: #F7F8FA;\n      --card: #FFFFFF;\n      --border: #E5E6EB;\n      --primary-text: #1D2129;\n      --secondary-text: #86909C;\n      --risk: #F54A45;\n      --risk-bg: #FFECE8;\n      --safe: #34B352;\n      --safe-bg: #E8FFF3;\n      --shadow: 0 2px 8px rgba(29, 33, 41, 0.06);\n      --shadow-hover: 0 6px 20px rgba(29, 33, 41, 0.08);\n    }\n    * { box-sizing: border-box; }\n    html, body { margin: 0; padding: 0; }\n    body {\n      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif;\n      background: var(--bg);\n      color: var(--primary-text);\n      -webkit-font-smoothing: antialiased;\n      font-size: 14px;\n      line-height: 1.5;\n    }\n    .container { max-width: 1400px; margin: 0 auto; padding: 16px 24px 24px; }\n\n    /* Top section wrapper with soft gradient */\n    .top-section {\n      background: linear-gradient(135deg, #B8D9A8 0%, #FFFFFF 100%);\n      border: 1px solid #E5E6EB;\n      border-radius: 16px;\n      padding: 12px;\n      margin-bottom: 14px;\n      box-shadow: var(--shadow);\n    }\n    .top-section .header-card {\n      background: transparent;\n      border: none;\n      box-shadow: none;\n      padding: 4px 12px 8px;\n      margin-bottom: 8px;\n    }\n    .top-section .metric-row {\n      margin-bottom: 0;\n    }\n    .top-section .metric-card {\n      background: rgba(255, 255, 255, 0.78);\n      backdrop-filter: blur(6px);\n      -webkit-backdrop-filter: blur(6px);\n      border-color: rgba(229, 230, 235, 0.9);\n    }\n\n    /* Header */\n    .header-card {\n      background: linear-gradient(135deg, #F0FBF3 0%, #FFFFFF 60%);\n      border: 1px solid var(--border);\n      border-radius: 12px;\n      padding: 24px 28px;\n      margin-bottom: 16px;\n      box-shadow: var(--shadow);\n    }\n    .header-card h1 {\n      margin: 0 0 4px;\n      font-size: 20px;\n      font-weight: 700;\n      color: var(--primary-text);\n    }\n    .header-meta {\n      color: var(--secondary-text);\n      font-size: 13px;\n    }\n    .header-meta strong { color: var(--primary-text); font-weight: 600; }\n    .header-meta .risk-tag { color: var(--risk); font-weight: 600; }\n    .header-meta a { color: #2A6AF0; text-decoration: none; font-weight: 600; }\n    .header-meta a:hover { text-decoration: underline; }\n\n    /* Metric cards */\n    .metric-row {\n      display: grid;\n      grid-template-columns: repeat(4, 1fr);\n      gap: 16px;\n      margin-bottom: 16px;\n    }\n    .metric-card {\n      background: var(--card);\n      border: 1px solid var(--border);\n      border-radius: 12px;\n      padding: 8px 16px;\n      box-shadow: var(--shadow);\n    }\n    .metric-card .label {\n      color: var(--secondary-text);\n      font-size: 13px;\n      margin-bottom: 2px;\n    }\n    .metric-card .value {\n      font-size: 21px;\n      font-weight: 700;\n      color: var(--primary-text);\n    }\n    .metric-card.risk .value { color: var(--risk); }\n\n    /* Toolbar */\n    .toolbar {\n      background: var(--card);\n      border: 1px solid var(--border);\n      border-radius: 12px;\n      padding: 10px 16px;\n      margin-bottom: 12px;\n      display: flex;\n      flex-wrap: wrap;\n      gap: 12px;\n      align-items: center;\n      box-shadow: var(--shadow);\n    }\n    .toolbar .field {\n      display: flex;\n      align-items: center;\n      gap: 8px;\n      font-size: 13px;\n    }\n    .toolbar label { color: var(--secondary-text); }\n    .toolbar input, .toolbar select {\n      border: 1px solid var(--border);\n      border-radius: 8px;\n      padding: 8px 12px;\n      font-size: 13px;\n      color: var(--primary-text);\n      background: #fff;\n      outline: none;\n      transition: border-color 0.2s;\n    }\n    .toolbar input:focus, .toolbar select:focus { border-color: var(--safe); }\n    .toolbar input[type="search"] { width: 240px; }\n    .toolbar .view-switch {\n      display: inline-flex;\n      border: 1px solid var(--border);\n      border-radius: 8px;\n      overflow: hidden;\n    }\n    .toolbar .view-switch button {\n      background: transparent;\n      border: none;\n      padding: 8px 14px;\n      font-size: 13px;\n      cursor: pointer;\n      color: var(--primary-text);\n      transition: all 0.2s;\n    }\n    .toolbar .view-switch button.active {\n      background: var(--safe);\n      color: #fff;\n      font-weight: 600;\n    }\n    .toolbar .view-switch button.risk-mode.active {\n      background: var(--risk);\n    }\n    .toolbar .spacer { flex: 1; }\n\n    /* Pagination */\n    .pagination {\n      display: flex;\n      align-items: center;\n      gap: 12px;\n      padding: 2px 20px 10px;\n      color: var(--secondary-text);\n      font-size: 13px;\n    }\n    .pagination button {\n      border: 1px solid var(--border);\n      background: #fff;\n      border-radius: 6px;\n      padding: 6px 12px;\n      cursor: pointer;\n      font-size: 13px;\n      color: var(--primary-text);\n      transition: all 0.15s;\n    }\n    .pagination button:hover:not(:disabled) { border-color: var(--safe); color: var(--safe); }\n    .pagination button:disabled { opacity: 0.45; cursor: not-allowed; }\n\n    /* Card grid */\n    .card-grid {\n      display: grid;\n      grid-template-columns: repeat(4, 1fr);\n      gap: 16px;\n    }\n    @media (max-width: 1200px) { .card-grid { grid-template-columns: repeat(3, 1fr); } .metric-row { grid-template-columns: repeat(2, 1fr); } }\n    @media (max-width: 900px) { .card-grid { grid-template-columns: repeat(2, 1fr); } }\n    @media (max-width: 600px) { .card-grid { grid-template-columns: 1fr; } .metric-row { grid-template-columns: 1fr; } }\n\n    .person-card {\n      background: var(--card);\n      border: 1px solid var(--border);\n      border-radius: 12px;\n      padding: 10px 14px;\n      box-shadow: var(--shadow);\n      transition: box-shadow 0.2s, transform 0.2s;\n      position: relative;\n      display: flex;\n      flex-direction: column;\n      gap: 6px;\n    }\n    .person-card:hover { box-shadow: var(--shadow-hover); transform: translateY(-1px); }\n    .person-card.risk {\n      border: 1.5px solid var(--risk);\n    }\n\n    .person-header {\n      display: flex;\n      justify-content: space-between;\n      align-items: flex-start;\n      gap: 10px;\n    }\n    .person-name {\n      font-size: 16px;\n      font-weight: 700;\n      color: var(--primary-text);\n      word-break: break-all;\n    }\n    .risk-badge {\n      background: var(--risk);\n      color: #fff;\n      font-size: 12px;\n      padding: 3px 10px;\n      border-radius: 999px;\n      font-weight: 600;\n      white-space: nowrap;\n      flex-shrink: 0;\n    }\n    .person-meta {\n      color: var(--secondary-text);\n      font-size: 12px;\n      display: flex;\n      flex-wrap: wrap;\n      gap: 6px 12px;\n      align-items: center;\n    }\n    .person-meta .tag {\n      background: #F2F3F5;\n      color: #4E5969;\n      padding: 2px 8px;\n      border-radius: 4px;\n      font-size: 12px;\n    }\n    .person-meta .tag.independent-yes { background: #E8FFF3; color: #34B352; }\n    .person-meta .tag.independent-no { background: #FFECE8; color: #F54A45; }\n\n    .chart-box { height: var(--chart-h, 150px); margin: 4px 0 0; transition: height 0.15s; }\n\n    .stat-row {\n      display: grid;\n      grid-template-columns: repeat(4, 1fr);\n      gap: 6px;\n      padding-top: 6px;\n      border-top: 1px dashed var(--border);\n    }\n    .stat-cell {\n      display: flex;\n      flex-direction: column;\n      gap: 2px;\n    }\n    .stat-cell .k {\n      color: var(--secondary-text);\n      font-size: 11px;\n    }\n    .stat-cell .v {\n      font-size: 14px;\n      font-weight: 600;\n      color: var(--primary-text);\n    }\n    .stat-cell .v.risk { color: var(--risk); }\n\n    .warn-line {\n      background: var(--risk-bg);\n      color: var(--risk);\n      padding: 4px 10px;\n      border-radius: 6px;\n      font-size: 12px;\n      font-weight: 500;\n      display: flex;\n      align-items: center;\n      gap: 6px;\n    }\n\n    .empty {\n      background: var(--card);\n      border: 1px dashed var(--border);\n      border-radius: 12px;\n      padding: 60px 20px;\n      text-align: center;\n      color: var(--secondary-text);\n      font-size: 14px;\n    }\n  </style>\n</head>\n<body>\n  <div class="container">\n    <div class="top-section">\n    <div class="header-card">\n      <h1>质检爬坡准确率 · 人员趋势看板</h1>\n      <div class="header-meta">\n        数据范围：<strong id="date-range"></strong>（<strong id="date-count"></strong> 天） · 总人数 <strong id="total-people"></strong> · 风险阈值：<span class="risk-tag">均值 &lt; 90%</span> · 生成时间 <strong id="gen-time"></strong> · 数据源：<a href="https://bytedance.larkoffice.com/wiki/Gx6DwNchOi3kIMkH9yGcB9ZFnTb?sheet=Gn4y0M" target="_blank" rel="noopener">飞书 Wiki · 质检（爬坡）准确率子表</a>\n      </div>\n    </div>\n\n    <div class="metric-row">\n      <div class="metric-card">\n        <div class="label">日期天数</div>\n        <div class="value"><span id="m-date-count"></span> 天</div>\n      </div>\n      <div class="metric-card">\n        <div class="label">总人数</div>\n        <div class="value" id="m-total"></div>\n      </div>\n      <div class="metric-card">\n        <div class="label">整体平均准确率</div>\n        <div class="value" id="m-mean"></div>\n      </div>\n      <div class="metric-card risk">\n        <div class="label">风险人数（均值 &lt; 90%）</div>\n        <div class="value" id="m-risk"></div>\n      </div>\n    </div>\n    </div><!-- /top-section -->\n\n    <div class="toolbar">\n      <div class="field">\n        <label>搜索</label>\n        <input id="search" type="search" placeholder="按姓名过滤，如 吴佳锡" />\n      </div>\n      <div class="field">\n        <label>队列</label>\n        <select id="queue-filter"><option value="">全部</option></select>\n      </div>\n      <div class="field">\n        <label>方向</label>\n        <select id="direction-filter"><option value="">全部</option></select>\n      </div>\n      <div class="field">\n        <label>排序</label>\n        <select id="sort-select">\n          <option value="risk-asc">风险优先 · 均值升序</option>\n          <option value="mean-asc">均值升序</option>\n          <option value="mean-desc">均值降序</option>\n          <option value="name">按姓名</option>\n        </select>\n      </div>\n      <div class="field">\n        <label>视图</label>\n        <div class="view-switch" id="view-switch">\n          <button data-view="all" class="active">全部人员</button>\n          <button data-view="risk" class="risk-mode">仅看风险</button>\n        </div>\n      </div>\n      <div class="field">\n        <label>时间范围</label>\n        <select id="range-select">\n          <option value="7" selected>最近 7 天</option>\n          <option value="14">最近 14 天</option>\n          <option value="30">最近 30 天</option>\n          <option value="all">全部</option>\n        </select>\n      </div>\n      <div class="spacer"></div>\n      <div class="field">\n        <label>每页</label>\n        <select id="page-size">\n          <option value="24" selected>24</option>\n          <option value="12">12</option>\n          <option value="48">48</option>\n        </select>\n      </div>\n    </div>\n\n    <div class="pagination" id="pagination-top">\n      <button id="prev-btn">‹ 上一页</button>\n      <span id="page-info">1 / 1</span>\n      <button id="next-btn">下一页 ›</button>\n    </div>\n\n    <div id="grid" class="card-grid"></div>\n    <div id="empty" class="empty" style="display:none;">没有匹配的人员</div>\n\n    <div class="pagination" id="pagination-bottom" style="justify-content:center; padding-top:16px;">\n      <button id="prev-btn2">‹ 上一页</button>\n      <span id="page-info2">1 / 1</span>\n      <button id="next-btn2">下一页 ›</button>\n    </div>\n  </div>\n\n  <script>\n    const DATA = __DASHBOARD_DATA__;\n\n    // ---- Init header + metrics ----\n    document.getElementById(\'date-range\').textContent = DATA.dateRangeText;\n    document.getElementById(\'date-count\').textContent = DATA.dateCount;\n    document.getElementById(\'total-people\').textContent = DATA.totalPeople;\n    document.getElementById(\'gen-time\').textContent = DATA.generatedTime;\n    document.getElementById(\'m-date-count\').textContent = DATA.dateCount;\n    document.getElementById(\'m-total\').textContent = DATA.totalPeople;\n    document.getElementById(\'m-mean\').textContent = DATA.overallMean.toFixed(2) + \'%\';\n    document.getElementById(\'m-risk\').textContent = DATA.riskCount;\n\n    const queueSelect = document.getElementById(\'queue-filter\');\n    DATA.queues.forEach(q => {\n      const opt = document.createElement(\'option\');\n      opt.value = q; opt.textContent = q;\n      queueSelect.appendChild(opt);\n    });\n    const dirSelect = document.getElementById(\'direction-filter\');\n    DATA.directions.forEach(d => {\n      const opt = document.createElement(\'option\');\n      opt.value = d; opt.textContent = d;\n      dirSelect.appendChild(opt);\n    });\n\n    const state = {\n      keyword: \'\',\n      queue: \'\',\n      direction: \'\',\n      sort: \'risk-asc\',\n      view: \'all\',\n      pageSize: 24,\n      page: 1,\n      rangeDays: 7,\n    };\n\n    const chartInstances = new Map();\n\n    function getRange() {\n      const total = DATA.shortDates.length;\n      let n = (state.rangeDays === \'all\') ? total : Math.min(parseInt(state.rangeDays, 10), total);\n      const start = Math.max(0, total - n);\n      return { start, end: total, count: n };\n    }\n\n    function statsForPerson(p) {\n      const { start, end } = getRange();\n      const scoresSlice = p.scores.slice(start, end);\n      const valid = scoresSlice.filter(v => v !== null && v !== undefined);\n      const mean = valid.length ? +(valid.reduce((a, b) => a + b, 0) / valid.length).toFixed(2) : null;\n      let latest = null;\n      for (let i = scoresSlice.length - 1; i >= 0; i--) {\n        if (scoresSlice[i] !== null && scoresSlice[i] !== undefined) { latest = scoresSlice[i]; break; }\n      }\n      const lowest = valid.length ? Math.min.apply(null, valid) : null;\n      const highest = valid.length ? Math.max.apply(null, valid) : null;\n      return { scoresSlice, valid, mean, latest, lowest, highest, validCount: valid.length };\n    }\n\n    function isRiskMean(mean) {\n      return mean !== null && mean !== undefined && mean < 90;\n    }\n\n    function isRisk(p, statsMap) {\n      const s = statsMap ? statsMap.get(p) : null;\n      const m = s ? s.mean : p.mean;\n      return isRiskMean(m);\n    }\n\n    function filterAndSort(statsMap) {\n      const kw = state.keyword.trim().toLowerCase();\n      let list = DATA.people.filter(p => {\n        if (state.view === \'risk\' && !isRisk(p, statsMap)) return false;\n        if (kw && !p.name.toLowerCase().includes(kw)) return false;\n        if (state.queue && p.queue !== state.queue) return false;\n        if (state.direction && p.direction !== state.direction) return false;\n        return true;\n      });\n      const meanOf = (p) => {\n        const s = statsMap.get(p);\n        return s ? s.mean : null;\n      };\n      const meanOr = (p, fallback) => {\n        const m = meanOf(p);\n        return (m === null || m === undefined) ? fallback : m;\n      };\n      switch (state.sort) {\n        case \'risk-asc\':\n          list.sort((a,b) => {\n            const ra = isRisk(a, statsMap) ? 0 : 1;\n            const rb = isRisk(b, statsMap) ? 0 : 1;\n            if (ra !== rb) return ra - rb;\n            return meanOr(a, 999) - meanOr(b, 999);\n          });\n          break;\n        case \'mean-asc\':\n          list.sort((a,b) => meanOr(a, 999) - meanOr(b, 999));\n          break;\n        case \'mean-desc\':\n          list.sort((a,b) => meanOr(b, -1) - meanOr(a, -1));\n          break;\n        case \'name\':\n          list.sort((a,b) => a.name.localeCompare(b.name, \'zh-CN\'));\n          break;\n      }\n      return list;\n    }\n\n    function fmtPct(v) {\n      if (v === null || v === undefined) return \'-\';\n      return v.toFixed(2) + \'%\';\n    }\n\n    function buildChart(el, person, stats) {\n      const risk = isRiskMean(stats.mean);\n      const color = risk ? \'#F54A45\' : \'#34B352\';\n      const areaColor = risk ? \'rgba(245, 74, 69, 0.18)\' : \'rgba(52, 179, 82, 0.18)\';\n      const areaColorEnd = risk ? \'rgba(245, 74, 69, 0.02)\' : \'rgba(52, 179, 82, 0.02)\';\n\n      const { start, end } = getRange();\n      const shortDatesSlice = DATA.shortDates.slice(start, end);\n      const headersSlice = DATA.dateHeaders.slice(start, end);\n      const dataPoints = stats.scoresSlice.map(v => (v === null || v === undefined) ? null : v);\n\n      const option = {\n        grid: { left: 4, right: 4, top: 12, bottom: 36, containLabel: true },\n        tooltip: {\n          trigger: \'axis\',\n          confine: true,\n          textStyle: { fontSize: 12 },\n          formatter: (params) => {\n            if (!params || !params.length) return \'\';\n            const p = params[0];\n            const raw = p.data;\n            const val = (raw === null || raw === undefined) ? \'-\' : (raw.toFixed(2) + \'%\');\n            return `${headersSlice[p.dataIndex]}<br/>准确率：<b>${val}</b>`;\n          }\n        },\n        xAxis: {\n          type: \'category\',\n          data: shortDatesSlice,\n          boundaryGap: false,\n          axisLine: { lineStyle: { color: \'#E5E6EB\' } },\n          axisTick: { show: false },\n          axisLabel: {\n            color: \'#86909C\',\n            fontSize: 9,\n            interval: 0,\n            rotate: 45,\n            margin: 8,\n          }\n        },\n        yAxis: {\n          type: \'value\',\n          min: 0,\n          max: 100,\n          show: false,\n        },\n        series: [{\n          type: \'line\',\n          data: dataPoints,\n          connectNulls: true,\n          smooth: false,\n          showSymbol: true,\n          symbolSize: 4,\n          lineStyle: { color, width: 2 },\n          itemStyle: { color },\n          areaStyle: {\n            color: {\n              type: \'linear\', x: 0, y: 0, x2: 0, y2: 1,\n              colorStops: [\n                { offset: 0, color: areaColor },\n                { offset: 1, color: areaColorEnd },\n              ]\n            }\n          },\n          markLine: {\n            symbol: \'none\',\n            silent: true,\n            label: { show: false },\n            lineStyle: { color: \'#F54A45\', type: \'dashed\', width: 1 },\n            data: [{ yAxis: 90 }]\n          }\n        }]\n      };\n      const chart = echarts.init(el, null, { renderer: \'canvas\' });\n      chart.setOption(option);\n      return chart;\n    }\n\n    function renderCard(person, stats) {\n      const risk = isRiskMean(stats.mean);\n      const el = document.createElement(\'div\');\n      el.className = \'person-card\' + (risk ? \' risk\' : \'\');\n\n      const indClass = person.independent === \'是\' ? \'independent-yes\' : (person.independent === \'否\' ? \'independent-no\' : \'\');\n\n      const parts = [];\n      parts.push(`\n        <div class="person-header">\n          <div class="person-name">${escapeHtml(person.name)}</div>\n          ${risk ? \'<span class="risk-badge">△ 风险</span>\' : \'\'}\n        </div>\n        <div class="person-meta">\n          ${person.queue ? `<span class="tag">${escapeHtml(person.queue)}</span>` : \'\'}\n          ${person.direction ? `<span class="tag">${escapeHtml(person.direction)}</span>` : \'\'}\n          ${person.independent ? `<span class="tag ${indClass}">独立质检：${escapeHtml(person.independent)}</span>` : \'\'}\n          <span style="color:#86909C;">${stats.validCount} 个数据点</span>\n        </div>\n        <div class="chart-box"></div>\n        <div class="stat-row">\n          <div class="stat-cell"><span class="k">均值</span><span class="v ${risk ? \'risk\' : \'\'}">${fmtPct(stats.mean)}</span></div>\n          <div class="stat-cell"><span class="k">最新</span><span class="v">${fmtPct(stats.latest)}</span></div>\n          <div class="stat-cell"><span class="k">最低</span><span class="v">${fmtPct(stats.lowest)}</span></div>\n          <div class="stat-cell"><span class="k">最高</span><span class="v">${fmtPct(stats.highest)}</span></div>\n        </div>\n        ${risk ? `<div class="warn-line">△ 均值 ${stats.mean.toFixed(2)}% &lt; 90%</div>` : \'\'}\n      `);\n      el.innerHTML = parts.join(\'\');\n      return el;\n    }\n\n    function escapeHtml(s) {\n      return String(s).replace(/[&<>"\']/g, c => ({\n        \'&\': \'&amp;\', \'<\': \'&lt;\', \'>\': \'&gt;\', \'"\': \'&quot;\', "\'": \'&#39;\'\n      }[c]));\n    }\n\n    function updateTopMetrics(statsMap) {\n      const { count } = getRange();\n      const means = [];\n      let riskCnt = 0;\n      DATA.people.forEach(p => {\n        const s = statsMap.get(p);\n        if (s && s.mean !== null && s.mean !== undefined) {\n          means.push(s.mean);\n          if (s.mean < 90) riskCnt++;\n        }\n      });\n      const overall = means.length ? +(means.reduce((a,b)=>a+b,0)/means.length).toFixed(2) : 0;\n      document.getElementById(\'m-date-count\').textContent = count;\n      document.getElementById(\'m-mean\').textContent = overall.toFixed(2) + \'%\';\n      document.getElementById(\'m-risk\').textContent = riskCnt;\n    }\n\n    function firstRowCount(grid) {\n      const children = grid.children;\n      if (!children.length) return 0;\n      const top0 = children[0].offsetTop;\n      let n = 0;\n      for (let i = 0; i < children.length; i++) {\n        if (children[i].offsetTop === top0) n++; else break;\n      }\n      return n;\n    }\n\n    // Adapt chart height so the top area + first card row fit within the\n    // visible viewport at 100% browser zoom, without needing to scroll.\n    function autoFitCharts() {\n      const grid = document.getElementById(\'grid\');\n      if (!grid.children.length) return;\n      const cols = firstRowCount(grid) || 1;\n      const firstRow = Array.from(grid.children).slice(0, cols);\n      const chartBox0 = firstRow[0].querySelector(\'.chart-box\');\n      if (!chartBox0) return;\n      const curChartH = chartBox0.getBoundingClientRect().height || 150;\n      // Height a card needs besides its chart (name/meta/stats/warn-line/padding).\n      // Measured per card so risk cards (with warn-line) are accounted for.\n      let maxOverhead = 0;\n      firstRow.forEach(card => {\n        const h = card.getBoundingClientRect().height;\n        maxOverhead = Math.max(maxOverhead, h - curChartH);\n      });\n      // Distance from the top of the grid to the bottom of the viewport.\n      const gridTop = grid.getBoundingClientRect().top; // page is at scroll-top on load\n      const avail = window.innerHeight - gridTop - 28; // breathing room so bottom numbers aren\'t at the edge\n      let target = Math.floor(avail - maxOverhead);\n      target = Math.max(64, Math.min(180, target));\n      const cur = parseFloat(getComputedStyle(document.documentElement).getPropertyValue(\'--chart-h\')) || 150;\n      if (Math.abs(target - cur) >= 2) {\n        document.documentElement.style.setProperty(\'--chart-h\', target + \'px\');\n      }\n      chartInstances.forEach(c => c.resize());\n    }\n\n    function render() {\n      // Dispose all charts\n      chartInstances.forEach(c => c.dispose());\n      chartInstances.clear();\n\n      const statsMap = new Map();\n      DATA.people.forEach(p => statsMap.set(p, statsForPerson(p)));\n      updateTopMetrics(statsMap);\n\n      const list = filterAndSort(statsMap);\n      const totalPages = Math.max(1, Math.ceil(list.length / state.pageSize));\n      if (state.page > totalPages) state.page = totalPages;\n      const start = (state.page - 1) * state.pageSize;\n      const end = start + state.pageSize;\n      const pageList = list.slice(start, end);\n\n      const grid = document.getElementById(\'grid\');\n      grid.innerHTML = \'\';\n      const empty = document.getElementById(\'empty\');\n      if (list.length === 0) {\n        empty.style.display = \'block\';\n      } else {\n        empty.style.display = \'none\';\n      }\n\n      pageList.forEach(p => {\n        const s = statsMap.get(p);\n        const card = renderCard(p, s);\n        grid.appendChild(card);\n        const chartBox = card.querySelector(\'.chart-box\');\n        const chart = buildChart(chartBox, p, s);\n        chartInstances.set(p.name, chart);\n      });\n\n      const pageInfo = `${state.page} / ${totalPages}（共 ${list.length} 人）`;\n      document.getElementById(\'page-info\').textContent = pageInfo;\n      document.getElementById(\'page-info2\').textContent = pageInfo;\n      const disablePrev = state.page <= 1;\n      const disableNext = state.page >= totalPages;\n      document.getElementById(\'prev-btn\').disabled = disablePrev;\n      document.getElementById(\'prev-btn2\').disabled = disablePrev;\n      document.getElementById(\'next-btn\').disabled = disableNext;\n      document.getElementById(\'next-btn2\').disabled = disableNext;\n\n      // Fit the first card row into the viewport after layout settles.\n      requestAnimationFrame(autoFitCharts);\n    }\n\n    // ---- Bind events ----\n    document.getElementById(\'search\').addEventListener(\'input\', (e) => {\n      state.keyword = e.target.value;\n      state.page = 1;\n      render();\n    });\n    document.getElementById(\'queue-filter\').addEventListener(\'change\', (e) => {\n      state.queue = e.target.value;\n      state.page = 1;\n      render();\n    });\n    document.getElementById(\'direction-filter\').addEventListener(\'change\', (e) => {\n      state.direction = e.target.value;\n      state.page = 1;\n      render();\n    });\n    document.getElementById(\'sort-select\').addEventListener(\'change\', (e) => {\n      state.sort = e.target.value;\n      render();\n    });\n    document.getElementById(\'page-size\').addEventListener(\'change\', (e) => {\n      state.pageSize = parseInt(e.target.value, 10);\n      state.page = 1;\n      render();\n    });\n    document.getElementById(\'range-select\').addEventListener(\'change\', (e) => {\n      const v = e.target.value;\n      state.rangeDays = (v === \'all\') ? \'all\' : parseInt(v, 10);\n      state.page = 1;\n      render();\n    });\n    document.querySelectorAll(\'#view-switch button\').forEach(btn => {\n      btn.addEventListener(\'click\', () => {\n        document.querySelectorAll(\'#view-switch button\').forEach(b => b.classList.remove(\'active\'));\n        btn.classList.add(\'active\');\n        state.view = btn.dataset.view;\n        state.page = 1;\n        render();\n      });\n    });\n    [\'prev-btn\', \'prev-btn2\'].forEach(id => {\n      document.getElementById(id).addEventListener(\'click\', () => {\n        if (state.page > 1) { state.page--; render(); window.scrollTo({top: 0, behavior: \'smooth\'}); }\n      });\n    });\n    [\'next-btn\', \'next-btn2\'].forEach(id => {\n      document.getElementById(id).addEventListener(\'click\', () => {\n        state.page++;\n        render();\n        window.scrollTo({top: 0, behavior: \'smooth\'});\n      });\n    });\n\n    let resizeTimer = null;\n    window.addEventListener(\'resize\', () => {\n      chartInstances.forEach(c => c.resize());\n      clearTimeout(resizeTimer);\n      resizeTimer = setTimeout(autoFitCharts, 150);\n    });\n\n    render();\n  </script>\n</body>\n</html>'


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as e:
        die("网络请求失败，请检查网络或飞书服务状态", str(e))
    except SystemExit:
        raise
    except Exception as e:
        die("脚本执行出错", f"{type(e).__name__}: {e}")
