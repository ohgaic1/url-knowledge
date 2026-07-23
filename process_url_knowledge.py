"""
URLナレッジDB 処理スクリプト
STEP1: URLなし＆本文なしの空ページを削除
STEP2: URLありページにサマリー・タグを追加（Claude Haiku + Jina AI）
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import anthropic
import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
# 出力を即時フラッシュ
import functools
print = functools.partial(print, flush=True)

# ── 設定 ──────────────────────────────────────────────
load_dotenv(Path(r"C:\dev\url-knowledge\.env"))
NOTION_TOKEN = (
    os.getenv("NOTION_TOKEN")
    or os.getenv("NOTION_API_KEY")
    or os.getenv("NOTION_SECRET")
)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

if not NOTION_TOKEN:
    raise ValueError("NOTION_TOKEN / NOTION_API_KEY / NOTION_SECRET が .env に見つかりません")
if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY が .env に見つかりません")

NOTION_DB_ID = "33967ba3044781859526f501a0e5b44b"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

VALID_TAGS = [
    "相続・遺言", "民事信託", "空き家・不動産", "建設業許可", "医療法人",
    "交通事故・後遺障害", "会社設立・法人", "税務・会計", "AI・テクノロジー",
    "マーケティング", "法改正・判例", "投資・資産運用", "不動産投資",
    "PC・ガジェット", "補助金・助成金", "行政手続き", "経営・起業", "その他",
]

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ── Notion ヘルパー ──────────────────────────────────

def get_all_pages() -> list[dict]:
    """DBの全ページをページネーション対応で取得"""
    pages = []
    cursor = None
    while True:
        payload: dict = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        resp = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query",
            headers=NOTION_HEADERS,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        pages.extend(data["results"])
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return pages


def get_blocks(page_id: str) -> list[dict]:
    resp = requests.get(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers=NOTION_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def archive_page(page_id: str) -> None:
    resp = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
        json={"archived": True},
        timeout=30,
    )
    resp.raise_for_status()


def update_page(page_id: str, summary: str, tags: list[str]) -> None:
    """タグとサマリーをプロパティとして更新（PATCH のみ使用）"""
    props: dict = {}
    if tags:
        props["タグ"] = {"multi_select": [{"name": t} for t in tags]}
    if summary:
        props["サマリー"] = {
            "rich_text": [{"type": "text", "text": {"content": summary[:2000]}}]
        }
    if not props:
        return
    r = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
        json={"properties": props},
        timeout=30,
    )
    if not r.ok:
        raise ValueError(f"プロパティ更新失敗 {r.status_code}: {r.text[:200]}")


def has_existing_summary(page: dict) -> bool:
    """サマリープロパティが既に入っていればTrue"""
    summary_prop = page.get("properties", {}).get("サマリー", {})
    rich_text = summary_prop.get("rich_text", [])
    return len(rich_text) > 0


# ── 外部API ─────────────────────────────────────────

def fetch_jina(url: str) -> str | None:
    """Jina AIで記事本文を取得（先頭5000文字）"""
    try:
        resp = requests.get(
            f"https://r.jina.ai/{url}",
            headers={"Accept": "text/plain", "X-No-Cache": "true"},
            timeout=20,
        )
        if resp.status_code == 200 and resp.text.strip():
            return resp.text[:5000]
    except Exception:
        pass
    return None


def generate_summary_tags(title: str, content: str) -> tuple[str, list[str]]:
    """Claude Haiku でサマリー3行とタグを生成"""
    tags_str = "\n".join(f"- {t}" for t in VALID_TAGS)
    prompt = f"""以下の記事タイトルと本文から、日本語で3行サマリーとタグを生成してください。
JSONのみ返してください（余分な説明文不要）。

タイトル: {title}

本文（抜粋）:
{content[:3000] if content else "（本文なし）"}

選択可能なタグ（最大3つ）:
{tags_str}

出力形式:
{{"summary": "1行目の要約。\\n2行目の要約。\\n3行目の要約。", "tags": ["タグ1", "タグ2"]}}"""

    msg = claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if not m:
        raise ValueError(f"JSON解析失敗: {text[:100]}")
    result = json.loads(m.group(0))
    summary = result.get("summary", "").strip()
    tags = [t for t in result.get("tags", []) if t in VALID_TAGS]
    return summary, tags


# ── STEP 1 ───────────────────────────────────────────

def step1_delete_empty(pages: list[dict]) -> int:
    print("\n" + "=" * 50)
    print("STEP1: 空ページの削除")
    print("=" * 50)
    deleted = 0
    checked = 0

    for page in pages:
        page_id = page["id"]
        props = page.get("properties", {})

        # URLがある場合はスキップ
        if props.get("URL", {}).get("url"):
            continue

        checked += 1
        title_list = props.get("Name", {}).get("title", [])
        title = title_list[0].get("plain_text", "") if title_list else ""

        try:
            blocks = get_blocks(page_id)
            if len(blocks) == 0:
                archive_page(page_id)
                deleted += 1
                print(f"  [削除] {title[:70] or '(無題)'}")
                time.sleep(0.3)
            # ブロックがあっても本文なし扱いにしたい場合は以下を有効化
            # else:
            #     has_text = any(
            #         block.get(block.get("type",""), {}).get("rich_text")
            #         for block in blocks
            #     )
            #     if not has_text: ...
        except Exception as e:
            print(f"  [ERR] {title[:50]} → {e}")

    print(f"\n  URLなしページ確認: {checked} 件 | 削除: {deleted} 件")
    return deleted


# ── STEP 2 ───────────────────────────────────────────

def step2_add_summaries(pages: list[dict]) -> None:
    print("\n" + "=" * 50)
    print("STEP2: サマリー・タグ追加")
    print("=" * 50)
    total = success = failed = skipped = 0

    for i, page in enumerate(pages, 1):
        page_id = page["id"]
        props = page.get("properties", {})

        url = props.get("URL", {}).get("url")
        if not url:
            continue

        total += 1
        title_list = props.get("Name", {}).get("title", [])
        title = title_list[0].get("plain_text", "") if title_list else url[:60]

        # 既存サマリーチェック（API呼び出し不要：pageオブジェクトから直接判定）
        if has_existing_summary(page):
            skipped += 1
            print(f"[{total:4}] SKIP  {title[:60]}")
            time.sleep(0.3)
            continue

        # Jina AI
        content = fetch_jina(url)
        if content is None:
            failed += 1
            print(f"[{total:4}] JINA NG  {title[:60]}")
            time.sleep(1)
            continue

        # Claude Haiku
        try:
            summary, tags = generate_summary_tags(title, content)
            if not summary:
                raise ValueError("空のサマリー")
            update_page(page_id, summary, tags)
            success += 1
            tag_str = ", ".join(tags) if tags else "-"
            print(f"[{total:4}] OK  {title[:55]}  [{tag_str}]")
        except Exception as e:
            failed += 1
            print(f"[{total:4}] Claude NG  {title[:55]} → {e}")

        time.sleep(1)

    print(f"\n  対象    : {total} 件")
    print(f"  成功    : {success} 件")
    print(f"  スキップ : {skipped} 件（既存サマリーあり）")
    print(f"  失敗    : {failed} 件")


# ── メイン ───────────────────────────────────────────

def main() -> None:
    print("URLナレッジDB 処理スクリプト")
    print("全ページ取得中...")
    pages = get_all_pages()
    print(f"取得完了: {len(pages)} 件")

    step1_delete_empty(pages)

    print("\n最新ページリスト再取得中（削除後）...")
    pages = get_all_pages()
    print(f"取得完了: {len(pages)} 件")

    step2_add_summaries(pages)

    print("\n=== 全処理完了 ===")


if __name__ == "__main__":
    main()
