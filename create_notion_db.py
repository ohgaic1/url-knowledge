"""
Notion に URLナレッジDB を作成するスクリプト。
NOTION_API_KEY を .env から読み込む（python-dotenv 不要・手動パース）。
"""

import os
import json
import requests
from pathlib import Path


# ─── .env 読み込み（python-dotenv なしで動作） ────────────────
def load_env_file(*paths: str) -> dict:
    for p in paths:
        env_path = Path(p)
        if env_path.exists():
            print(f"[INFO] .env を読み込み: {env_path}")
            env = {}
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip().strip('"').strip("'")
            return env
    return {}


env_vars = load_env_file(
    r"C:\dev\url-knowledge\.env",
    r"C:\dev\notion-setup\.env",
)

NOTION_API_KEY = env_vars.get("NOTION_API_KEY") or os.environ.get("NOTION_API_KEY", "")

if not NOTION_API_KEY:
    raise SystemExit(
        "[ERROR] NOTION_API_KEY が見つかりません。\n"
        "C:\\dev\\url-knowledge\\.env に NOTION_API_KEY=secret_... を記載してください。"
    )

# ─── 設定 ─────────────────────────────────────────────────────
PARENT_PAGE_ID = "33567ba304478172a1a5d66df161214c"
DB_NAME = "URLナレッジDB"

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# ─── DB 作成ペイロード ─────────────────────────────────────────
TAG_OPTIONS = [
    {"name": "相続・遺言",           "color": "purple"},
    {"name": "民事信託",             "color": "blue"},
    {"name": "空き家・不動産",       "color": "green"},
    {"name": "建設業許可",           "color": "orange"},
    {"name": "医療法人",             "color": "red"},
    {"name": "交通事故・後遺障害",   "color": "pink"},
    {"name": "会社設立・法人",       "color": "yellow"},
    {"name": "税務・会計",           "color": "brown"},
    {"name": "AI・テクノロジー",     "color": "gray"},
    {"name": "マーケティング",       "color": "default"},
    {"name": "法改正・判例",         "color": "blue"},
    {"name": "その他",               "color": "default"},
]

payload = {
    "parent": {"type": "page_id", "page_id": PARENT_PAGE_ID},
    "title": [{"type": "text", "text": {"content": DB_NAME}}],
    "properties": {
        "Name": {"title": {}},
        "URL": {"url": {}},
        "タグ": {
            "multi_select": {
                "options": TAG_OPTIONS
            }
        },
        "保存日": {"date": {}},
    },
}

# ─── API 呼び出し ─────────────────────────────────────────────
print(f"[INFO] Notion DB 作成中: {DB_NAME}")
resp = requests.post(
    "https://api.notion.com/v1/databases",
    headers=HEADERS,
    json=payload,
    timeout=30,
)

if not resp.ok:
    raise SystemExit(f"[ERROR] Notion API エラー {resp.status_code}:\n{resp.text}")

db = resp.json()
db_id = db["id"]
db_url = db.get("url", "")

print()
print("=" * 60)
print(f"  [完了] データベース作成完了")
print(f"  DB名         : {DB_NAME}")
print(f"  Database ID  : {db_id}")
print(f"  URL          : {db_url}")
print("=" * 60)
print()
print(f'# .env または app.py に貼り付け用:')
print(f'NOTION_DATABASE_ID={db_id.replace("-", "")}')
