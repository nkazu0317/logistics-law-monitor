#!/usr/bin/env python3
"""
物流効率化法サイト自動監視スクリプト
毎日実行され、変更があればClaude APIで解析
"""

import os
import hashlib
import requests
from datetime import datetime
from pathlib import Path
import json

# =============================================================================
# 設定
# =============================================================================
TARGET_URL = "https://www.mlit.go.jp/seisakutokatsu/freight/seisakutokatsu_freight_mn1_000029.html"
SNAPSHOT_DIR = Path("snapshots")
REPORTS_DIR = Path("reports")

# ディレクトリ作成
SNAPSHOT_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

# =============================================================================
# ユーティリティ関数
# =============================================================================

def fetch_page(url: str) -> str:
    """ウェブページを取得"""
    print(f"📥 ページを取得中: {url}")
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.text

def calculate_hash(text: str) -> str:
    """テキストのMD5ハッシュを計算"""
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def get_latest_snapshot() -> tuple:
    """最新のスナップショットを取得"""
    snapshots = sorted(SNAPSHOT_DIR.glob("snapshot_*.txt"))
    
    if not snapshots:
        print("📝 初回実行です（過去のスナップショットなし）")
        return None, None, None
    
    latest_file = snapshots[-1]
    with open(latest_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # ハッシュ値を取得（メタデータファイルから）
    meta_file = latest_file.with_suffix('.json')
    if meta_file.exists():
        with open(meta_file, 'r', encoding='utf-8') as f:
            meta = json.load(f)
            old_hash = meta.get('hash')
    else:
        old_hash = calculate_hash(content)
    
    print(f"📂 前回スナップショット: {latest_file.name}")
    return str(latest_file), content, old_hash

def save_snapshot(content: str, content_hash: str) -> str:
    """スナップショットを保存"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = SNAPSHOT_DIR / f"snapshot_{timestamp}.txt"
    meta_filename = SNAPSHOT_DIR / f"snapshot_{timestamp}.json"
    
    # 本文保存
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)
    
    # メタデータ保存
    meta = {
        "timestamp": timestamp,
        "url": TARGET_URL,
        "hash": content_hash,
        "size_bytes": len(content.encode('utf-8'))
    }
    with open(meta_filename, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    
    print(f"💾 スナップショット保存: {filename.name}")
    return str(filename)

# =============================================================================
# Claude API連携
# =============================================================================

def analyze_with_claude(old_text: str, new_text: str) -> dict:
    """Claude APIで差分を解析"""
    api_key = os.getenv('CLAUDE_API_KEY')
    
    if not api_key:
        print("⚠️  CLAUDE_API_KEYが設定されていません")
        print("   GitHub Secretsに設定してください")
        return None
    
    print("🤖 Claude APIで解析中...")
    
    try:
        import anthropic
        
        client = anthropic.Anthropic(api_key=api_key)
        
        # プロンプト構築
        if old_text is None:
            prompt_type = "初回取得"
            old_excerpt = "なし（初回）"
        else:
            prompt_type = "差分解析"
            old_excerpt = old_text[:2000]
        
        prompt = f"""あなたは国土交通省の物流効率化法に詳しい実務専門家です。

【タスク】
物流効率化法のウェブページを解析し、重要な情報を抽出してください。

【{prompt_type}】

旧版（抜粋）:
{old_excerpt}

新版（抜粋）:
{new_text[:2000]}

【出力形式】
以下のJSON形式で出力してください：

{{
  "analysis_date": "{datetime.now().strftime('%Y-%m-%d')}",
  "change_detected": true/false,
  "change_summary": "変更の概要を3〜5行で説明",
  "key_points": [
    "重要ポイント1",
    "重要ポイント2",
    "重要ポイント3"
  ],
  "stakeholder_impact": {{
    "荷主": "荷主への影響",
    "運送事業者": "運送事業者への影響",
    "軽トラック事業者": "軽トラック事業者への影響"
  }},
  "important_dates": {{
    "施行日": "YYYY-MM-DD",
    "その他重要日": "YYYY-MM-DD"
  }},
  "action_items": [
    {{
      "対象者": "荷主/運送事業者/軽トラック事業者",
      "アクション": "具体的な行動",
      "期限": "YYYY-MM-DD"
    }}
  ],
  "confidence": "high/medium/low"
}}

【注意】
- 推測の場合は confidence を "low" に設定
- 日付が不明な場合は "未定" と記載
- 根拠が明確な情報のみを記載"""

        message = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4096,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )
        
        response_text = message.content[0].text
        
        # JSON抽出
        json_start = response_text.find('{')
        json_end = response_text.rfind('}') + 1
        
        if json_start >= 0 and json_end > json_start:
            analysis = json.loads(response_text[json_start:json_end])
            print("✅ Claude API解析完了")
            return analysis
        else:
            print("⚠️  JSON形式の抽出に失敗")
            return {"error": "JSON parsing failed", "raw": response_text}
            
    except Exception as e:
        print(f"❌ Claude APIエラー: {e}")
        return {"error": str(e)}

# =============================================================================
# レポート生成
# =============================================================================

def generate_markdown_report(analysis: dict) -> str:
    """Markdown形式のレポートを生成"""
    
    md = f"""# 物流効率化法 監視レポート

**最終更新**: {datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}  
**対象URL**: [{TARGET_URL}]({TARGET_URL})

---

## 📋 解析結果

### 変更検知

"""
    
    if analysis.get('change_detected'):
        md += "🆕 **変更が検出されました**\n\n"
    else:
        md += "✨ 変更なし（前回から更新なし）\n\n"
    
    # 変更概要
    if analysis.get('change_summary'):
        md += f"### 変更概要\n\n{analysis['change_summary']}\n\n"
    
    # 重要ポイント
    if analysis.get('key_points'):
        md += "### 重要ポイント\n\n"
        for i, point in enumerate(analysis['key_points'], 1):
            md += f"{i}. {point}\n"
        md += "\n"
    
    # ステークホルダー別影響
    if analysis.get('stakeholder_impact'):
        md += "## 👥 対象者別の影響\n\n"
        for stakeholder, impact in analysis['stakeholder_impact'].items():
            md += f"### {stakeholder}\n\n{impact}\n\n"
    
    # 重要日程
    if analysis.get('important_dates'):
        md += "## 📅 重要な日程\n\n"
        md += "| 項目 | 日付 |\n"
        md += "|------|------|\n"
        for item, date in analysis['important_dates'].items():
            md += f"| {item} | {date} |\n"
        md += "\n"
    
    # アクション項目
    if analysis.get('action_items'):
        md += "## ✅ 必要なアクション\n\n"
        md += "| 対象者 | アクション | 期限 |\n"
        md += "|--------|-----------|------|\n"
        for item in analysis['action_items']:
            md += f"| {item.get('対象者', '-')} | {item.get('アクション', '-')} | {item.get('期限', '-')} |\n"
        md += "\n"
    
    # 信頼度
    confidence = analysis.get('confidence', 'unknown')
    confidence_emoji = {
        'high': '🟢',
        'medium': '🟡',
        'low': '🔴',
        'unknown': '⚪'
    }
    md += f"## 📊 解析信頼度\n\n{confidence_emoji.get(confidence, '⚪')} **{confidence.upper()}**\n\n"
    
    # フッター
    md += "---\n\n"
    md += "*このレポートは自動生成されました。最新情報は必ず[公式サイト](https://www.mlit.go.jp/seisakutokatsu/freight/seisakutokatsu_freight_mn1_000029.html)でご確認ください。*\n"
    
    return md

def save_reports(analysis: dict):
    """レポートを保存"""
    
    # Markdown保存（GitHub Pages用）
    md_report = generate_markdown_report(analysis)
    with open(REPORTS_DIR / 'index.md', 'w', encoding='utf-8') as f:
        f.write(md_report)
    
    # JSON保存（機械可読用）
    with open(REPORTS_DIR / 'latest.json', 'w', encoding='utf-8') as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)
    
    print("📄 レポート生成完了")

# =============================================================================
# 通知
# =============================================================================

def send_slack_notification(message: str):
    """Slack通知（オプション）"""
    webhook_url = os.getenv('SLACK_WEBHOOK_URL')
    
    if not webhook_url:
        return
    
    try:
        payload = {
            "text": f"🚨 物流効率化法サイト更新\n\n{message}\n\n詳細: {TARGET_URL}"
        }
        response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
        print("📢 Slack通知送信完了")
    except Exception as e:
        print(f"⚠️  Slack通知失敗: {e}")

# =============================================================================
# メイン処理
# =============================================================================

def main():
    """メイン処理"""
    print("=" * 60)
    print("🔍 物流効率化法サイト監視を開始")
    print("=" * 60)
    print()
    
    try:
        # 1. ページ取得
        current_content = fetch_page(TARGET_URL)
        current_hash = calculate_hash(current_content)
        print(f"   ハッシュ値: {current_hash[:16]}...")
        print()
        
        # 2. 前回スナップショット取得
        old_file, old_content, old_hash = get_latest_snapshot()
        print()
        
        # 3. 変更検知
        if old_hash and current_hash == old_hash:
            print("✨ 変更なし - 処理を終了します")
            
            # 変更なしでも簡単なレポートを更新
            no_change_analysis = {
                "analysis_date": datetime.now().strftime('%Y-%m-%d'),
                "change_detected": False,
                "change_summary": "前回チェックから変更はありませんでした。",
                "confidence": "high"
            }
            save_reports(no_change_analysis)
            return
        
        print("🆕 変更を検出しました！")
        print()
        
        # 4. スナップショット保存
        save_snapshot(current_content, current_hash)
        print()
        
        # 5. Claude APIで解析
        analysis = analyze_with_claude(old_content, current_content)
        print()
        
        if analysis and 'error' not in analysis:
            # 6. レポート生成
            save_reports(analysis)
            print()
            
            # 7. 通知
            summary = analysis.get('change_summary', '変更が検出されました')
            send_slack_notification(summary)
            print()
        else:
            print("⚠️  解析結果が不完全です")
            if analysis:
                print(f"   エラー: {analysis.get('error', 'unknown')}")
        
        print("=" * 60)
        print("✅ 監視処理が完了しました")
        print("=" * 60)
        
    except Exception as e:
        print()
        print("=" * 60)
        print(f"❌ エラーが発生しました: {e}")
        print("=" * 60)
        raise

if __name__ == "__main__":
    main()
