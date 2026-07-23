"""
Google Keep エクスポートデータを Notion URLナレッジDB に一括移行するスクリプト
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# Windows ターミナルの文字コードエラーを防ぐ
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 設定 ──────────────────────────────────────────────
KEEP_FOLDER = Path(r"C:\Users\ohgai\Downloads\takeout-keep\Takeout\Keep")
NOTION_DB_ID = "33967ba3044781859526f501a0e5b44b"
RATE_LIMIT_WAIT = 0.3  # 秒
START_INDEX = 161     # 既登録済みの件数（0から再実行する場合は 0 に戻す）

# .env 読み込み
load_dotenv(Path(r"C:\dev\url-knowledge\.env"))
NOTION_TOKEN = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY") or os.getenv("NOTION_SECRET")
if not NOTION_TOKEN:
    raise ValueError(".env に NOTION_TOKEN / NOTION_API_KEY / NOTION_SECRET が見つかりません")

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# ── Keep ラベル → Notion タグ マッピング ──────────────
LABEL_MAP = {
    "AI":            "AI・テクノロジー",
    "テクノロジー":   "AI・テクノロジー",
    "相続":          "相続・遺言",
    "遺言":          "相続・遺言",
    "信託":          "民事信託",
    "不動産":        "空き家・不動産",
    "空き家":        "空き家・不動産",
    "建設業":        "建設業許可",
    "医療":          "医療法人",
    "交通事故":      "交通事故・後遺障害",
    "後遺障害":      "交通事故・後遺障害",
    "会社":          "会社設立・法人",
    "法人":          "会社設立・法人",
    "税務":          "税務・会計",
    "会計":          "税務・会計",
    "簿記":          "税務・会計",
    "マーケティング": "マーケティング",
    "法改正":        "法改正・判例",
    "判例":          "法改正・判例",
    "セキュリティ":  "セキュリティ・法令遵守",
    "経営":          "経営・起業",
    "起業":          "経営・起業",
}

URL_PATTERN = re.compile(r'https?://[^\s\'"<>]+')


def extract_url(data: dict) -> str | None:
    """annotations または textContent から URL を抽出"""
    # 1) annotations の weblink を優先
    for ann in data.get("annotations", []):
        if ann.get("source") == "WEBLINK" and ann.get("url"):
            return ann["url"]
    # 2) textContent から正規表現で抽出
    text = data.get("textContent", "")
    m = URL_PATTERN.search(text)
    if m:
        return m.group(0)
    return None


def map_labels(labels: list[dict]) -> list[str]:
    """Keep ラベルを Notion タグに変換"""
    tags = set()
    for label in labels:
        name = label.get("name", "")
        for key, notion_tag in LABEL_MAP.items():
            if key in name:
                tags.add(notion_tag)
                break
    return list(tags)


def usec_to_iso_date(usec: int) -> str:
    """マイクロ秒タイムスタンプを ISO-8601 日付文字列 (YYYY-MM-DD) に変換"""
    dt = datetime.fromtimestamp(usec / 1_000_000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def clean_title(title: str) -> str:
    """タイトルの前後の引用符・余分な空白を除去"""
    title = title.strip().strip('"').strip("'").strip()
    # SmartNews サフィックス除去
    title = re.sub(r'\s+#SmartNews\s*$', '', title)
    return title[:2000]  # Notion タイトル上限


def create_notion_page(title: str, url: str, tags: list[str], saved_date: str) -> dict:
    """Notion API でページを作成"""
    properties: dict = {
        "Name": {
            "title": [{"type": "text", "text": {"content": title}}]
        },
        "保存日": {
            "date": {"start": saved_date}
        },
    }
    if url:
        properties["URL"] = {"url": url}
    if tags:
        properties["タグ"] = {
            "multi_select": [{"name": t} for t in tags]
        }

    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": properties,
    }
    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=HEADERS,
        json=payload,
        timeout=30,
    )
    return resp


def main():
    json_files = sorted(KEEP_FOLDER.glob("*.json"))
    total = len(json_files)
    success = 0
    skipped = 0
    failed = 0
    errors = []

    print(f"=== Google Keep → Notion URLナレッジDB 移行 ===")
    print(f"対象ファイル: {total} 件  (開始インデックス: {START_INDEX})\n")

    for i, path in enumerate(json_files, 1):
        if i <= START_INDEX:
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        # ゴミ箱は除外
        if data.get("isTrashed", False):
            skipped += 1
            print(f"[{i:3}/{total}] SKIP (Trashed): {path.name}")
            continue

        raw_title = data.get("title", "").strip()
        url = extract_url(data)
        labels = data.get("labels", [])
        tags = map_labels(labels)
        created_usec = data.get("createdTimestampUsec", 0)
        saved_date = usec_to_iso_date(created_usec) if created_usec else "2024-01-01"

        # タイトルがなければ URL または ファイル名をフォールバック
        if not raw_title:
            raw_title = url or path.stem
        title = clean_title(raw_title)

        resp = create_notion_page(title, url, tags, saved_date)

        if resp.status_code in (200, 201):
            success += 1
            tag_str = ", ".join(tags) if tags else "-"
            print(f"[{i:3}/{total}] OK  {title[:50]}  [{tag_str}]")
        else:
            failed += 1
            err_msg = resp.json().get("message", resp.text[:100])
            errors.append(f"{path.name}: {resp.status_code} {err_msg}")
            print(f"[{i:3}/{total}] NG  {title[:50]}  → {resp.status_code}: {err_msg}")

        time.sleep(RATE_LIMIT_WAIT)

    print(f"\n=== 処理完了 ===")
    print(f"  対象   : {total} 件")
    print(f"  成功   : {success} 件")
    print(f"  スキップ: {skipped} 件（ゴミ箱）")
    print(f"  失敗   : {failed} 件")
    if errors:
        print("\n--- エラー詳細 ---")
        for e in errors:
            print(f"  {e}")


if __name__ == "__main__":
    main()
