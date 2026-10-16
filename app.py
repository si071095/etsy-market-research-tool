import io
import os
import re
from collections import defaultdict
from typing import Dict, Tuple
from urllib.parse import quote_plus

import pandas as pd
import requests
import streamlit as st

API_BASE = "https://api.etsy.com/v3/application"

POSITIVE = [
    "tokyo", "japan", "japanese", "salaryman", "commuter", "station",
    "subway", "train", "yamanote", "office", "overtime", "work",
    "corporate", "night", "late", "retro", "coffee", "cat", "ramen",
    "samurai", "sushi", "vintage", "streetwear", "kanji"
]
NEGATIVE = [
    "halloween", "ghost", "christian", "church", "retirement",
    "clarksville", "png", "svg", "digital download", "template",
    "clipart", "sticker", "mug", "poster", "bundle", "file"
]
STOPWORDS = {
    "shirt", "shirts", "tshirt", "tshirts", "tee", "tees", "unisex",
    "men", "mens", "women", "womens", "gift", "gifts", "graphic",
    "design", "style", "top", "clothing", "apparel", "for", "and",
    "the", "a", "an", "with", "of", "in", "on", "to", "from",
    "by", "new", "cool", "funny", "cute"
}


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


def tokenize(text: str):
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def relevance_score(title: str, keyword: str) -> int:
    title_l = (title or "").lower()
    keyword_l = (keyword or "").lower()
    keyword_tokens = tokenize(keyword_l)
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


def etsy_search_url(keyword: str) -> str:
    return f"https://www.etsy.com/search?q={quote_plus(str(keyword))}"

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
        return df, {"検索キーワード": keyword, "Etsy検索件数": total_count, "取得件数": 0, "関連商品数": 0, "関連商品率(%)": 0, "価格中央値(USD)": None, "関連商品の総閲覧数": 0, "関連商品の総お気に入り数": 0, "お気に入り率(%)": None, "競合点": 0, "価格点": 0, "需要点": 0, "サンプル点": 0, "小市場補正": -20, "閲覧数補正": -10, "飽和補正": saturation_penalty(total_count), "関連度補正": -15, "参入期待度": 0, "評価": "D"}

    high = df[df["関連度"] == "High"].copy()
    high_ratio = len(high) / len(df) if len(df) else 0
    usd = high[high["通貨"] == "USD"].copy()
    usd_prices = pd.to_numeric(usd["価格"], errors="coerce").dropna()
    usd_median = round(usd_prices.median(), 2) if not usd_prices.empty else None
    favorites_series = pd.to_numeric(high["お気に入り数"], errors="coerce")
    views_series = pd.to_numeric(high["閲覧数"], errors="coerce")
    total_favorites = int(favorites_series.fillna(0).sum()) if not high.empty else 0
    total_views = int(views_series.fillna(0).sum()) if not high.empty else 0
    weighted_rate = round(total_favorites / total_views * 100, 3) if total_views > 0 else None
    avg_favorites = round(favorites_series.mean(), 2) if favorites_series.notna().any() else None
    max_favorites = int(favorites_series.max()) if favorites_series.notna().any() else None
    comp = competition_score(len(high))
    pscore = price_score(usd_median)
    dscore = demand_score(avg_favorites, max_favorites, weighted_rate, len(high))
    sscore = sample_score(len(df), total_count)
    tiny = tiny_market_penalty(len(high))
    saturated = saturation_penalty(total_count)
    rel_pen = relevance_penalty(high_ratio)
    views_penalty = -10 if total_views < 20 else -5 if total_views < 50 else 0
    opportunity = max(0, comp + pscore + dscore + sscore + tiny + saturated + rel_pen + views_penalty)
    summary = {
        "検索キーワード": keyword, "Etsy検索件数": total_count, "取得件数": len(df), "関連商品数": len(high), "関連商品率(%)": round(high_ratio * 100, 2),
        "価格中央値(USD)": usd_median, "関連商品の総閲覧数": total_views, "関連商品の総お気に入り数": total_favorites, "お気に入り率(%)": weighted_rate,
        "競合点": comp, "価格点": pscore, "需要点": dscore, "サンプル点": sscore, "小市場補正": tiny, "閲覧数補正": views_penalty,
        "飽和補正": saturated, "関連度補正": rel_pen, "参入期待度": opportunity, "評価": opportunity_rank(opportunity)
    }
    return df, summary


def extract_candidate_terms(listings_df: pd.DataFrame) -> pd.DataFrame:
    if listings_df.empty:
        return pd.DataFrame()

    token_stats = defaultdict(lambda: {"count": 0, "views": 0, "favorites": 0})
    phrase_stats = defaultdict(lambda: {"count": 0, "views": 0, "favorites": 0})

    for _, row in listings_df.iterrows():
        title = str(row.get("商品タイトル", "") or "")
        views = int(row.get("閲覧数") or 0)
        favs = int(row.get("お気に入り数") or 0)
        tokens = [t for t in tokenize(title) if len(t) >= 3 and t not in STOPWORDS and not t.isdigit()]

        for token in set(tokens):
            token_stats[token]["count"] += 1
            token_stats[token]["views"] += views
            token_stats[token]["favorites"] += favs

        for i in range(len(tokens) - 1):
            phrase = f"{tokens[i]} {tokens[i+1]}"
            phrase_stats[phrase]["count"] += 1
            phrase_stats[phrase]["views"] += views
            phrase_stats[phrase]["favorites"] += favs

    rows = []
    def add_candidates(stats, kind):
        for term, s in stats.items():
            if s["count"] < 2:
                continue
            fav_rate = (s["favorites"] / s["views"] * 100) if s["views"] > 0 else 0
            score = min(s["count"] * 4, 28) + min(s["views"] / 100, 30) + min(s["favorites"] * 1.5, 27) + min(fav_rate * 1.5, 15)
            rows.append({
                "候補テーマ": term,
                "種別": kind,
                "出現商品数": s["count"],
                "合計閲覧数": s["views"],
                "合計お気に入り数": s["favorites"],
                "お気に入り率(%)": round(fav_rate, 2),
                "注目度": round(min(score, 100), 1),
                "Etsyで見る": etsy_search_url(term),
            })

    add_candidates(phrase_stats, "2語フレーズ")
    add_candidates(token_stats, "単語")

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows)
    generic = {"japanese", "japan", "tokyo", "vintage", "retro", "streetwear"}
    result = result[~result["候補テーマ"].isin(generic)]
    return result.sort_values(["注目度", "合計閲覧数", "合計お気に入り数"], ascending=[False, False, False]).drop_duplicates("候補テーマ").head(30)


def build_excel(summary_df, listings_df, discovery_df=None) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        if summary_df is not None and not summary_df.empty:
            summary_df.to_excel(writer, sheet_name="市場比較", index=False)
        if discovery_df is not None and not discovery_df.empty:
            discovery_df.to_excel(writer, sheet_name="売れ筋候補", index=False)
        if listings_df is not None and not listings_df.empty:
            listings_df.to_excel(writer, sheet_name="商品一覧", index=False)
    buffer.seek(0)
    return buffer.getvalue()


st.set_page_config(page_title="アパレル市場調査ツール", page_icon="📊", layout="wide")
st.title("📊 アパレル市場調査ツール")
st.caption("Etsyの公開商品データから、POD向けの市場候補を探します。")

with st.expander("📘 使い方", expanded=False):
    st.markdown("""
### 2つの使い方があります
**① 売れ筋を探す**  
何を検索すればいいか分からない場合はこちら。広めの検索から、閲覧・お気に入り反応が見えるテーマ候補を自動で探します。

**② キーワードを比較する**  
調べたいテーマが決まっている場合はこちら。複数の英語キーワードを比較し、競合・価格・需要などから「参入期待度」を出します。

※ Etsyの競合商品の実売上数は取得できないため、売れ筋候補は **Views / Favorites / Favorite Rate / 競合状況** から推定しています。
    """)

mode = st.radio("何をしますか？", ["🔥 売れ筋を探す", "🔎 キーワードを比較する"], horizontal=True)

if mode == "🔥 売れ筋を探す":
    st.subheader("🔥 売れ筋候補を自動で探す")
    st.write("何を検索すればいいか分からなくてもOKです。広めの検索から反応の良い言葉・テーマを抽出します。")
    st.caption("まずは候補から選ぶか、自由入力で広めのテーマを入れてください。")

    input_mode = st.radio("入力方法", ["候補から選ぶ", "自分で入力する"], horizontal=True)
    seed_map = {
        "日本系Tシャツ全般": ["japanese shirt", "japanese graphic tee", "funny japanese shirt", "japanese streetwear shirt"],
        "日本レトロ": ["japanese retro shirt", "retro japan shirt", "japanese vintage shirt", "japanese coffee shirt"],
        "東京・街": ["tokyo shirt", "tokyo train shirt", "tokyo nightlife shirt", "tokyo streetwear shirt"],
        "食・ラーメン": ["ramen shirt", "japanese food shirt", "sushi shirt", "japanese coffee shirt"],
        "猫・ペット": ["japanese cat shirt", "cat japan shirt", "japanese dog shirt", "kawaii cat shirt"],
    }

    if input_mode == "候補から選ぶ":
        seed_choice = st.selectbox("何系の商品を調べますか？", list(seed_map.keys()))
        seeds = seed_map[seed_choice]
        st.caption("使用する親キーワード：" + ", ".join(seeds))
    else:
        st.info("自由入力では、細かい商品名ではなく **広めのテーマ** を入れるのがおすすめです。例：camping shirt, fishing shirt, engineer shirt")
        seed_text = st.text_input(
            "調べたいジャンルを入力（カンマ区切り）",
            value="camping shirt, outdoor shirt, camper shirt",
            placeholder="例：camping shirt, fishing shirt, engineer shirt",
            help="スマホからも入力できます。細かすぎるキーワードではなく、広めのジャンルがおすすめです。"
        )
        with st.expander("自由入力のコツ", expanded=False):
            st.markdown(
                """
**良い例（広めのテーマ）**
- `camping shirt`
- `fishing shirt`
- `engineer shirt`
- `coffee shirt`

**細かすぎる例**
- `funny japanese salaryman overtime warrior shirt`
- `tokyo last train club retro graphic shirt`

まずは広めに入力して、出てきた候補をあとで比較分析するのがおすすめです。
                """
            )
        seeds = [x.strip() for x in seed_text.split(",") if x.strip()]

    if st.button("🔥 売れ筋候補を探す", type="primary", use_container_width=True):
        progress = st.progress(0)
        status = st.empty()
        all_listings, seed_summaries = [], []

        for i, seed in enumerate(seeds, start=1):
            status.write(f"調査中：**{seed}**")
            try:
                df, summary = analyze_keyword(seed)
                if not df.empty:
                    all_listings.append(df)
                seed_summaries.append(summary)
            except Exception as e:
                st.error(f"{seed}：{e}")
            progress.progress(i / len(seeds))

        progress.empty(); status.empty()
        listings_df = pd.concat(all_listings, ignore_index=True) if all_listings else pd.DataFrame()
        summary_df = pd.DataFrame(seed_summaries)
        discovery_df = extract_candidate_terms(listings_df)

        if discovery_df.empty:
            st.warning("候補を十分に抽出できませんでした。別の入口で試してください。")
        else:
            st.success("売れ筋候補の抽出が完了しました")
            top = discovery_df.iloc[0]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("注目候補", top["候補テーマ"])
            c2.metric("注目度", top["注目度"])
            c3.metric("関連商品数", int(top["出現商品数"]))
            c4.metric("お気に入り率", f'{top["お気に入り率(%)"]}%')

            st.subheader("注目テーマ候補 TOP15")
            st.dataframe(discovery_df.head(15), use_container_width=True, hide_index=True)
            st.info("この一覧は『実売上ランキング』ではなく、Etsy上で閲覧・お気に入り反応が見えるテーマ候補です。気になる候補を『キーワードを比較する』で再調査してください。")

            with st.expander("元になった商品を見る", expanded=False):
                st.dataframe(listings_df.sort_values(["閲覧数", "お気に入り数"], ascending=[False, False]), use_container_width=True, hide_index=True, column_config={"商品URL": st.column_config.LinkColumn("商品URL")})

            excel_bytes = build_excel(summary_df, listings_df, discovery_df)
            st.download_button("⬇️ 発掘結果をExcelでダウンロード", data=excel_bytes, file_name="etsy_discovery_result.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)

else:
    st.subheader("🔎 キーワードを比較する")
    preset = st.selectbox("調べたいジャンルの例", ["自由入力", "会社員・通勤", "東京・電車", "夜・終電", "日本レトロ"])
    preset_values = {
        "自由入力": "",
        "会社員・通勤": "salaryman shirt, japanese office shirt, overtime shirt, commuter shirt",
        "東京・電車": "tokyo train shirt, tokyo commuter shirt, yamanote shirt, tokyo station shirt",
        "夜・終電": "last train shirt, tokyo nightlife shirt, late night tokyo shirt, tokyo last train shirt",
        "日本レトロ": "japanese retro shirt, kissaten shirt, retro japan shirt, japanese coffee shirt",
    }
    default_text = preset_values[preset] if preset != "自由入力" else "salaryman shirt, tokyo train shirt, japanese office shirt"
    keywords_text = st.text_area("検索キーワード", value=default_text, height=100, help="英語推奨。複数の場合はカンマ区切りです。")

    if st.button("🔎 市場を分析する", type="primary", use_container_width=True):
        keywords = [x.strip() for x in keywords_text.split(",") if x.strip()]
        if not keywords:
            st.warning("検索キーワードを入力してください。")
            st.stop()

        summaries, detail_frames = [], []
        progress = st.progress(0); status = st.empty()
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
        status.empty(); progress.empty()

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
        st.bar_chart(summary_df[["検索キーワード", "参入期待度"]].set_index("検索キーワード"))

        with st.expander("スコアの内訳を見る", expanded=False):
            score_cols = ["検索キーワード", "競合点", "価格点", "需要点", "サンプル点", "小市場補正", "閲覧数補正", "飽和補正", "関連度補正", "参入期待度", "評価"]
            st.dataframe(summary_df[score_cols], use_container_width=True, hide_index=True)

        with st.expander("取得した商品を見る", expanded=False):
            st.dataframe(listings_df, use_container_width=True, hide_index=True, column_config={"商品URL": st.column_config.LinkColumn("商品URL")})

        excel_bytes = build_excel(summary_df, listings_df)
        st.download_button("⬇️ 分析結果をExcelでダウンロード", data=excel_bytes, file_name="etsy_market_research.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)

st.divider()
st.caption("※ 『売れ筋候補』『参入期待度』は、Etsyの公開データから算出した参考値です。競合商品の実売上数そのものではありません。")
st.caption("The term 'Etsy' is a trademark of Etsy, Inc. This application uses the Etsy API but is not endorsed or certified by Etsy, Inc.")
