import streamlit as st
import requests
import anthropic
import json
from datetime import date
from pathlib import Path

# ─── .env / st.secrets からキーを読み込む ────────────────────────
def _load_env_file(path: str) -> dict:
    """python-dotenv なしで .env を手動パース（フォールバック用）。"""
    p = Path(path)
    if not p.exists():
        return {}
    result = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        result[k.strip()] = v.strip().strip('"').strip("'")
    return result

# python-dotenv が使えれば使う
try:
    from dotenv import load_dotenv
    load_dotenv(r"C:\dev\url-knowledge\.env", override=False)
    _env = {}
except ImportError:
    _env = _load_env_file(r"C:\dev\url-knowledge\.env")

import os
_env_defaults = {
    "CLAUDE_API_KEY":  os.environ.get("CLAUDE_API_KEY",  _env.get("CLAUDE_API_KEY",  "")),
    "NOTION_API_KEY":  os.environ.get("NOTION_API_KEY",  _env.get("NOTION_API_KEY",  "")),
    "NOTION_DB_ID":    os.environ.get("NOTION_DB_ID",    _env.get("NOTION_DB_ID",    "")),
}
for k in _env_defaults:
    if not _env_defaults[k]:
        try:
            _env_defaults[k] = st.secrets.get(k, "")
        except Exception:
            pass

# ─── ページ設定 ───────────────────────────────────────────────
st.set_page_config(
    page_title="URL Knowledge Saver",
    page_icon="📚",
    layout="wide",
)

st.title("📚 URL Knowledge Saver")
st.caption("URLを貼って保存するだけ — タグ・スタイルはClaudeが自動判定します")

# ─── クエリパラメータからURLを受け取る ───────────────────────
_params = st.query_params
_url_from_param = _params.get("url", "")

# ─── サイドバー：API設定 & ブックマークレット ─────────────────
with st.sidebar:
    st.header("API設定")
    claude_api_key = st.text_input(
        "Claude API Key",
        value=_env_defaults["CLAUDE_API_KEY"],
        type="password",
        placeholder="sk-ant-...",
    )
    notion_api_key = st.text_input(
        "Notion API Key",
        value=_env_defaults["NOTION_API_KEY"],
        type="password",
        placeholder="ntn_... または secret_...",
    )
    notion_database_id = st.text_input(
        "Notion Database ID",
        value=_env_defaults["NOTION_DB_ID"],
        placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    )
    st.divider()
    st.caption("モデル: claude-opus-4-20250514")

    st.divider()
    st.subheader("Chrome ブックマークレット")
    st.caption("以下のコードをブックマークのURLに貼り付けると、閲覧中のページURLをアプリに送れます。")
    bookmarklet = (
        "javascript:(function(){"
        "var u=encodeURIComponent(location.href);"
        "window.open('http://localhost:8501/?url='+u,'_blank');"
        "})();"
    )
    st.code(bookmarklet, language=None)
    st.caption("使い方: ブックマークを新規作成 → 名前を「Save to Notion」→ URLに上記を貼り付け")

# ─── メイン入力エリア ─────────────────────────────────────────
url_input = st.text_input(
    "URL",
    value=_url_from_param,
    placeholder="https://example.com/article",
)

run_button = st.button("保存する", type="primary", use_container_width=True)

# ─── 定数 ────────────────────────────────────────────────────
ALL_STYLES = [
    "行政書士実務視点",
    "法律実務視点",
    "ビジネス視点",
    "投資・資産運用視点",
    "IT・テクノロジー視点",
    "学習メモ",
]

ALL_TAGS = [
    "相続・遺言", "民事信託", "空き家・不動産", "建設業許可", "医療法人",
    "交通事故・後遺障害", "会社設立・法人", "税務・会計", "AI・テクノロジー",
    "マーケティング", "法改正・判例", "投資・資産運用", "不動産投資",
    "PC・ガジェット", "セキュリティ・法令遵守", "補助金・助成金",
    "判例・裁判例", "行政手続き", "経営・起業", "その他",
]

STYLE_PERSONAS = {
    "行政書士実務視点": "行政書士業務（許認可・申請・書類作成・法令対応）の視点",
    "法律実務視点":     "法律・判例・規制の視点",
    "ビジネス視点":     "ビジネス戦略・経営・マーケティングの視点",
    "投資・資産運用視点": "投資・資産運用・財務の視点",
    "IT・テクノロジー視点": "IT・テクノロジー・エンジニアリングの視点",
    "学習メモ":         "学習・理解・記憶に役立つ形式",
}

SYSTEM_PROMPT = """あなたはWebページの内容を分析して知識メモを作成するアシスタントです。
行政書士事務所の実務に役立つ形で情報を整理してください。"""

_STYLES_LIST = "\n".join(f"- {s}" for s in ALL_STYLES)
_TAGS_LIST   = "\n".join(f"- {t}" for t in ALL_TAGS)

SUMMARY_INSTRUCTION_TEMPLATE = (
    "以下のWebページ本文を分析し、JSONのみを出力してください（マークダウンコードブロック不要）。\n\n"
    "## 自動判定ルール\n\n"
    "**サマリースタイル**（以下から最適な1つを選択）:\n"
    + _STYLES_LIST + "\n\n"
    "**タグ**（以下から最大3つを選択）:\n"
    + _TAGS_LIST + "\n\n"
    '## 出力フォーマット\n'
    '{\n'
    '  "style": "選択したスタイル名",\n'
    '  "tags": ["タグ1", "タグ2"],\n'
    '  "title": "記事のタイトル（30文字以内）",\n'
    '  "three_line_summary": "1行目\\n2行目\\n3行目",\n'
    '  "keywords": ["キーワード1", "キーワード2", "キーワード3", "キーワード4", "キーワード5"],\n'
    '  "detailed_memo": "詳細メモ（300〜500字程度、選択したスタイルの視点で記述）"\n'
    '}\n\n'
    "---本文---\n"
    "__CONTENT__"
)


# ─── ヘルパー関数 ─────────────────────────────────────────────

def fetch_content(url: str) -> str:
    """Jina AI Reader APIで本文テキストを取得する。"""
    jina_url = f"https://r.jina.ai/{url}"
    headers = {"Accept": "text/plain"}
    resp = requests.get(jina_url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def summarize_with_claude(content: str, api_key: str) -> dict:
    """Claude APIで要約・タグ・スタイルを自動判定し、dictを返す。"""
    client = anthropic.Anthropic(api_key=api_key)
    user_message = SUMMARY_INSTRUCTION_TEMPLATE.replace("__CONTENT__", content[:12000])

    message = client.messages.create(
        model="claude-opus-4-20250514",
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
        raw = raw.rstrip("`").strip()
    return json.loads(raw)


def save_to_notion(
    title: str,
    url: str,
    tags: list[str],
    summary_style: str,
    summary_data: dict,
    notion_api_key: str,
    database_id: str,
) -> str:
    """Notion APIにページを作成し、作成したページのURLを返す。"""
    today = date.today().isoformat()

    three_lines = summary_data.get("three_line_summary", "")
    keywords = summary_data.get("keywords", [])
    detailed_memo = summary_data.get("detailed_memo", "")

    payload = {
        "parent": {"database_id": database_id},
        "properties": {
            "Name": {
                "title": [{"text": {"content": title}}]
            },
            "URL": {
                "url": url
            },
            "タグ": {
                "multi_select": [{"name": t} for t in tags]
            },
            "保存日": {
                "date": {"start": today}
            },
        },
        "children": [
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "3行サマリー"}}]
                },
            },
            *[
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": line.strip()}}]
                    },
                }
                for line in three_lines.split("\n") if line.strip()
            ],
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "キーワード"}}]
                },
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": "  ".join(keywords)}}]
                },
            },
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "詳細メモ"}}]
                },
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": detailed_memo}}]
                },
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": f"スタイル: {summary_style} | 保存日: {today}"}}]
                },
            },
        ],
    }

    headers = {
        "Authorization": f"Bearer {notion_api_key}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=headers,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("url", "")


# ─── メイン処理 ───────────────────────────────────────────────

if run_button:
    missing = []
    if not url_input.strip():
        missing.append("URL")
    if not claude_api_key.strip():
        missing.append("Claude API Key")
    if not notion_api_key.strip():
        missing.append("Notion API Key")
    if not notion_database_id.strip():
        missing.append("Notion Database ID")

    if missing:
        st.error(f"以下の入力が必要です：{', '.join(missing)}")
        st.stop()

    st.divider()
    progress_placeholder = st.empty()
    result_placeholder = st.container()

    # ステップ 1：本文取得
    with progress_placeholder.container():
        st.info("**Step 1/3** — Jina AI で本文を取得中...")

    try:
        content = fetch_content(url_input.strip())
    except requests.HTTPError as e:
        progress_placeholder.error(f"本文取得失敗: {e}")
        st.stop()
    except Exception as e:
        progress_placeholder.error(f"本文取得エラー: {e}")
        st.stop()

    # ステップ 2：AI 要約（タグ・スタイル自動判定）
    with progress_placeholder.container():
        st.info("**Step 2/3** — Claude API で要約・タグ・スタイルを自動判定中...")

    try:
        summary_data = summarize_with_claude(content, claude_api_key.strip())
    except json.JSONDecodeError as e:
        progress_placeholder.error(f"JSON解析失敗: {e}")
        st.stop()
    except anthropic.APIError as e:
        progress_placeholder.error(f"Claude API エラー: {e}")
        st.stop()
    except Exception as e:
        progress_placeholder.error(f"要約エラー: {e}")
        st.stop()

    detected_style = summary_data.get("style", "学習メモ")
    detected_tags  = summary_data.get("tags", ["その他"])
    if isinstance(detected_tags, str):
        detected_tags = [detected_tags]

    # ステップ 3：Notion 保存
    with progress_placeholder.container():
        st.info("**Step 3/3** — Notion に保存中...")

    try:
        page_url = save_to_notion(
            title=summary_data.get("title", url_input[:30]),
            url=url_input.strip(),
            tags=detected_tags,
            summary_style=detected_style,
            summary_data=summary_data,
            notion_api_key=notion_api_key.strip(),
            database_id=notion_database_id.strip(),
        )
    except requests.HTTPError as e:
        progress_placeholder.error(f"Notion 保存失敗: {e.response.text}")
        st.stop()
    except Exception as e:
        progress_placeholder.error(f"Notion 保存エラー: {e}")
        st.stop()

    # 完了
    progress_placeholder.success("完了！Notion に保存しました。")

    with result_placeholder:
        st.subheader(summary_data.get("title", "（タイトルなし）"))

        # 自動判定結果バッジ
        badge_cols = st.columns(len(detected_tags) + 1)
        badge_cols[0].markdown(f"**スタイル**: {detected_style}")
        for i, t in enumerate(detected_tags, start=1):
            badge_cols[i].badge(t)

        st.markdown("### 3行サマリー")
        for line in summary_data.get("three_line_summary", "").split("\n"):
            if line.strip():
                st.markdown(f"- {line.strip()}")

        st.markdown("### キーワード")
        keywords = summary_data.get("keywords", [])
        if keywords:
            kw_cols = st.columns(len(keywords))
            for i, kw in enumerate(keywords):
                kw_cols[i].badge(kw)

        st.markdown("### 詳細メモ")
        st.markdown(summary_data.get("detailed_memo", ""))

        st.divider()
        if page_url:
            st.markdown(f"[Notion ページを開く]({page_url})")
