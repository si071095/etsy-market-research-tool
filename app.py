import io
import os
import re
from typing import Dict, Tuple

import pandas as pd
import requests
import streamlit as st

API_BASE = "https://api.etsy.com/v3/application"

POSITIVE = [
    "tokyo", "japan", "japanese", "salaryman", "commuter", "station",
    "subway", "train", "yamanote", "office", "overtime", "work",
    "corporate", "night", "late"
]

NEGATIVE = [
    "halloween", "ghost", "christian", "church", "retirement",
    "clarksville", "png", "svg", "digital download", "template",
    "clipart", "sticker", "mug", "poster"
]

def get_secret(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        try:
            value = str(st.secrets.get(name, "")).strip()
        except Exception:
            value = ""
    return value

def get_headers():
    keystring = get_secret("ETSY_KEYSTRING")
    shared_secret = get_secret("ETSY_SHARED_SECRET")
    if not keystring or not shared_secret:
        raise RuntimeError("Etsy APIの認証情報が未設定です。RenderのEnvironment Variablesを確認してください。")
    return {"x-api-key": f"{keystring}:{shared_secret}", "Accept": "application/json"}

def money_to_float(money):
    if not money:
        return None
    try:
        return float(money.get("amount")) / float(money.get("divisor", 100))
    except (TypeError, ValueError, ZeroDivisionError):
        return None

def relevance_score(title: str, keyword: str) -> int:
    title_l = (title or "").lower()
    keyword_l = (keyword or "").lower()
    keyword_tokens = re.findall(r"[a-z0-9]+", keyword_l)
    score = 0
    if keyword_tokens:
        hits = sum(1 for token in keyword_tokens if token in title_l)
        score += min(60, round(hits / len(keyword_tokens) * 60))
    if keyword_l and keyword_l in title_l:
        score += 20
    score += min(30, sum(1 for term in POSITIVE if term in title_l) * 6)
    score -= min(50, sum(1 for term in NEGATIVE if term in title_l) * 15)
    return max(0, min(100, score))

def relevance_band(score: int) -> str:
    return "High" if score >= 80 else "Medium" if score >= 50 else "Low"

def competition_score(n: int) -> int:
    if n <= 50: return 45
    if n <= 100: return 42
    if n <= 250: return 37
    if n <= 500: return 32
    if n <= 1000: return 25
    if n <= 2000: return 18
    if n <= 5000: return 10
    if n <= 10000: return 5
    return 2

def price_score(price):
    if price is None or pd.isna(price): return 0
    p = float(price)
    if 24 <= p <= 32: return 20
    if 20 <= p < 24 or 32 < p <= 36: return 17
    if 18 <= p < 20 or 36 < p <= 40: return 13
    if 15 <= p < 18 or 40 < p <= 50: return 8
    return 4

def demand_score(avg_favorites, max_favorites, weighted_rate, relevant_count):
    avg_favorites = 0 if avg_favorites is None or pd.isna(avg_favorites) else float(avg_favorites)
    max_favorites = 0 if max_favorites is None or pd.isna(max_favorites) else float(max_favorites)
    weighted_rate = 0 if weighted_rate is None or pd.isna(weighted_rate) else float(weighted_rate)
    avg_score = 8 if avg_favorites >= 50 else 6 if avg_favorites >= 20 else 5 if avg_favorites >= 10 else 3 if avg_favorites >= 5 else 1 if avg_favorites > 0 else 0
    max_score = 5 if max_favorites >= 1000 else 4 if max_favorites >= 500 else 3 if max_favorites >= 200 else 2 if max_favorites >= 100 else 1 if max_favorites > 0 else 0
    rate_score = 10 if weighted_rate >= 10 else 8 if weighted_rate >= 5 else 6 if weighted_rate >= 3 else 4 if weighted_rate >= 1.5 else 2 if weighted_rate > 0 else 0
    rel_score = 2 if relevant_count >= 25 else 1 if relevant_count >= 10 else 0
    return min(25, avg_score + max_score + rate_score + rel_score)

def sample_score(sample_count: int, total_count: int) -> int:
    if sample_count <= 0: return 0
    base = 7 if sample_count >= 100 else 6 if sample_count >= 75 else 5 if sample_count >= 50 else 4 if sample_count >= 25 else 3 if sample_count >= 10 else 1
    coverage = sample_count / total_count if total_count else 0
    bonus = 3 if coverage >= 0.8 else 2 if coverage >= 0.5 else 1 if coverage >= 0.1 else 0
    return min(10, base + bonus)

def tiny_market_penalty(n: int) -> int:
    if n <= 5: return -20
    if n <= 10: return -15
    if n <= 25: return -8
    if n <= 50: return -3
    return 0

def saturation_penalty(n: int) -> int:
    if n > 20000: return -20
    if n > 10000: return -15
    if n > 5000: return -10
    return 0

def relevance_penalty(high_ratio: float) -> int:
    if high_ratio >= 0.60: return 0
    if high_ratio >= 0.40: return -5
    if high_ratio >= 0.20: return -10
    return -15

def opportunity_rank(score: int) -> str:
    if score >= 80: return "S"
    if score >= 65: return "A"
    if score >= 50: return "B"
    if score >= 35: return "C"
    return "D"

def rank_comment(rank: str) -> str:
    return {
        "S": "かなり有望。商品化候補として優先確認。",
        "A": "有望。競合商品を確認しながらテスト候補へ。",
        "B": "可能性あり。切り口をさらに絞って再調査。",
        "C": "慎重。別キーワードやニッチ化を検討。",
        "D": "現状は優先度低め。別テーマを推奨。",
    }.get(rank, "")

def search_etsy(keyword: str, limit: int = 100) -> dict:
    response = requests.get(
        f"{API_BASE}/listings/active",
        headers=get_headers(),
        params={"keywords": keyword, "limit": min(max(limit, 1), 100), "sort_on": "score", "sort_order": "desc"},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Etsy API Error {response.status_code}: {response.text}")
    return response.json()

def analyze_keyword(keyword: str) -> Tuple[pd.DataFrame, Dict]:
    payload = search_etsy(keyword)
    rows = []
    for item in payload.get("results", []):
        title = item.get("title") or ""
        rel_score = relevance_score(title, keyword)
        views = item.get("views")
        favorites = item.get("num_favorers")
        favorite_rate = None
        try:
            if views and favorites is not None:
                favorite_rate = float(favorites) / float(views) * 100
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        price = item.get("price") or {}
        rows.append({
            "検索キーワード": keyword,
            "商品ID": item.get("listing_id"),
            "商品タイトル": title,
            "価格": money_to_float(price),
            "通貨": price.get("currency_code"),
            "お気に入り数": favorites,
            "閲覧数": views,
            "お気に入り率(%)": round(favorite_rate, 3) if favorite_rate is not None else None,
            "関連度スコア": rel_score,
            "関連度": relevance_band(rel_score),
            "商品URL": item.get("url"),
            "ショップID": item.get("shop_id"),
        })

    df = pd.DataFrame(rows)
    total_count = int(payload.get("count", 0) or 0)

    if df.empty:
        return df, {
            "検索キーワード": keyword, "Etsy検索件数": total_count, "取得件数": 0, "関連商品数": 0,
            "関連商品率(%)": 0, "価格中央値(USD)": None, "関連商品の総閲覧数": 0, "関連商品の総お気に入り数": 0,
            "お気に入り率(%)": None, "競合点": 0, "価格点": 0, "需要点": 0, "サンプル点": 0,
            "小市場補正": -20, "閲覧数補正": -10, "飽和補正": saturation_penalty(total_count),
            "関連度補正": -15, "参入期待度": 0, "評価": "D"
        }

    high = df[df["関連度"] == "High"].copy()
    high_ratio = len(high) / len(df) if len(df) else 0

    usd = high[high["通貨"] == "USD"].copy()
    usd_prices = pd.to_numeric(usd["価格"], errors="coerce").dropna()
    usd_median = round(usd_prices.median(), 2) if not usd_prices.empty else None

    favorites_series = pd.to_numeric(high["お気に入り数"], errors="coerce")
    views_series = pd.to_numeric(high["閲覧数"], errors="coerce")
    total_favorites = int(favorites_series.fillna(0).sum()) if not high.empty else 0
    total_views = int(views_series.fillna(0).sum()) if not high.empty else 0
    weighted_favorite_rate = round(total_favorites / total_views * 100, 3) if total_views > 0 else None

    avg_favorites = round(favorites_series.mean(), 2) if favorites_series.notna().any() else None
    max_favorites = int(favorites_series.max()) if favorites_series.notna().any() else None

    comp = competition_score(len(high))
    pscore = price_score(usd_median)
    dscore = demand_score(avg_favorites, max_favorites, weighted_favorite_rate, len(high))
    sscore = sample_score(len(df), total_count)
    tiny = tiny_market_penalty(len(high))
    saturated = saturation_penalty(total_count)
    rel_pen = relevance_penalty(high_ratio)
    views_penalty = -10 if total_views < 20 else -5 if total_views < 50 else 0
    opportunity = max(0, comp + pscore + dscore + sscore + tiny + saturated + rel_pen + views_penalty)

    summary = {
        "検索キーワード": keyword,
        "Etsy検索件数": total_count,
        "取得件数": len(df),
        "関連商品数": len(high),
        "関連商品率(%)": round(high_ratio * 100, 2),
        "価格中央値(USD)": usd_median,
        "関連商品の総閲覧数": total_views,
        "関連商品の総お気に入り数": total_favorites,
        "お気に入り率(%)": weighted_favorite_rate,
        "競合点": comp,
        "価格点": pscore,
        "需要点": dscore,
        "サンプル点": sscore,
        "小市場補正": tiny,
        "閲覧数補正": views_penalty,
        "飽和補正": saturated,
        "関連度補正": rel_pen,
        "参入期待度": opportunity,
        "評価": opportunity_rank(opportunity),
    }
    return df, summary

def build_excel(summary_df: pd.DataFrame, listings_df: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="市場比較", index=False)
        listings_df.to_excel(writer, sheet_name="商品一覧", index=False)
    buffer.seek(0)
    return buffer.getvalue()

st.set_page_config(page_title="アパレル市場調査ツール", page_icon="📊", layout="wide")
st.title("📊 アパレル市場調査ツール")
st.caption("Etsyの公開商品データから、POD向けの市場候補を比較します。")

st.info("競合数・価格・閲覧数・お気に入り数・検索結果の関連度をもとに「参入期待度」を算出します。スコアは商品化候補を絞るための参考値です。")

preset = st.selectbox("調査テーマの例", ["自由入力", "会社員・通勤", "東京・電車", "夜・終電", "日本レトロ"])
preset_values = {
    "自由入力": "",
    "会社員・通勤": "salaryman shirt, japanese office shirt, overtime shirt, commuter shirt",
    "東京・電車": "tokyo train shirt, tokyo commuter shirt, yamanote shirt, tokyo station shirt",
    "夜・終電": "last train shirt, tokyo nightlife shirt, late night tokyo shirt, tokyo last train shirt",
    "日本レトロ": "japanese retro shirt, kissaten shirt, retro japan shirt, japanese coffee shirt",
}

default_text = preset_values[preset] if preset != "自由入力" else "salaryman shirt, tokyo train shirt, japanese office shirt"

keywords_text = st.text_area("検索キーワード", value=default_text, height=100, help="複数検索する場合はカンマ区切りで入力してください。")
analyze_button = st.button("🔎 市場を分析する", type="primary", use_container_width=True)

if analyze_button:
    keywords = [x.strip() for x in keywords_text.split(",") if x.strip()]
    if not keywords:
        st.warning("検索キーワードを入力してください。")
        st.stop()

    summaries, detail_frames = [], []
    progress = st.progress(0)
    status = st.empty()

    for idx, keyword in enumerate(keywords, start=1):
        status.write(f"分析中：**{keyword}**")
        try:
            detail_df, summary = analyze_keyword(keyword)
            summaries.append(summary)
            if not detail_df.empty:
                detail_frames.append(detail_df)
        except Exception as e:
            st.error(f"{keyword}：{e}")
        progress.progress(idx / len(keywords))

    status.empty()
    progress.empty()

    if not summaries:
        st.error("取得できたデータがありませんでした。")
        st.stop()

    summary_df = pd.DataFrame(summaries).sort_values(by=["参入期待度", "関連商品数"], ascending=[False, False])
    listings_df = pd.concat(detail_frames, ignore_index=True) if detail_frames else pd.DataFrame()

    if not listings_df.empty:
        listings_df = listings_df.sort_values(by=["検索キーワード", "関連度スコア", "お気に入り数"], ascending=[True, False, False])

    st.success("分析完了")
    top = summary_df.iloc[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("トップ候補", top["検索キーワード"])
    c2.metric("参入期待度", int(top["参入期待度"]))
    c3.metric("評価", top["評価"])
    c4.metric("関連商品数", int(top["関連商品数"]))

    st.markdown(f"**判定コメント：** {rank_comment(top['評価'])}")

    st.subheader("市場比較")
    visible_cols = ["検索キーワード", "参入期待度", "評価", "Etsy検索件数", "関連商品数", "関連商品率(%)", "価格中央値(USD)", "関連商品の総閲覧数", "関連商品の総お気に入り数", "お気に入り率(%)"]
    st.dataframe(summary_df[visible_cols], use_container_width=True, hide_index=True)

    st.subheader("参入期待度")
    chart_df = summary_df[["検索キーワード", "参入期待度"]].set_index("検索キーワード")
    st.bar_chart(chart_df)

    with st.expander("スコアの内訳を見る", expanded=False):
        score_cols = ["検索キーワード", "競合点", "価格点", "需要点", "サンプル点", "小市場補正", "閲覧数補正", "飽和補正", "関連度補正", "参入期待度", "評価"]
        st.dataframe(summary_df[score_cols], use_container_width=True, hide_index=True)
        st.caption("競合点45点・価格点20点・需要点25点・サンプル点10点を基本に、市場規模・閲覧数・競合過多・検索ノイズを補正しています。")

    with st.expander("取得した商品を見る", expanded=False):
        st.caption("関連度Highの商品を中心に確認してください。最終の商品企画では、商標・著作権・デザイン類似も必ず目視確認してください。")
        st.dataframe(listings_df, use_container_width=True, hide_index=True, column_config={"商品URL": st.column_config.LinkColumn("商品URL")})

    excel_bytes = build_excel(summary_df, listings_df)
    st.download_button("⬇️ 分析結果をExcelでダウンロード", data=excel_bytes, file_name="etsy_market_research.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)

st.divider()

with st.expander("評価の見方", expanded=False):
    st.markdown(
        "**S**：かなり有望。商品化候補として優先確認  \n"
        "**A**：有望。競合商品を確認しながらテスト候補へ  \n"
        "**B**：可能性あり。切り口をさらに絞って再調査  \n"
        "**C**：慎重。別キーワードやニッチ化を検討  \n"
        "**D**：現状は優先度低め"
    )

st.caption("※ 本ツールのスコアは市場候補を比較するための参考値です。売上を保証するものではありません。")
st.caption("The term 'Etsy' is a trademark of Etsy, Inc. This application uses the Etsy API but is not endorsed or certified by Etsy, Inc.")
