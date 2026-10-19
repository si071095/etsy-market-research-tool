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
    # 商品カテゴリ・衣類そのもの
    "shirt", "shirts", "tshirt", "tshirts", "tee", "tees",
    "hoodie", "hoodies", "sweatshirt", "sweatshirts",
    "jacket", "jackets", "coat", "coats", "sweater", "sweaters",
    "crewneck", "crewnecks", "pullover", "pullovers",
    "tank", "tanks", "tanktop", "tanktops", "top", "tops",
    "clothing", "apparel", "wear", "wears",

    # 購買・属性系の汎用語
    "unisex", "men", "mens", "women", "womens", "male", "female",
    "gift", "gifts", "adult", "adults", "youth", "kids", "kid",

    # テーマとして弱い汎用語
    "graphic", "design", "style", "printed", "print", "prints",
    "front", "back", "oversized", "custom", "personalized",
    "aesthetic", "trendy", "classic", "premium", "soft",

    # 英語ストップワード
    "for", "and", "the", "a", "an", "with", "of", "in", "on",
    "to", "from", "by", "new", "cool", "funny", "cute"
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
    """
    Etsy検索リンク用。
    売れ筋発掘で "aesthetic" のような一般語だけが候補になった場合でも、
    Tシャツ市場を確認できるよう apparel系の語が無ければ "shirt" を付ける。
    """
    keyword = str(keyword).strip()
    lower = keyword.lower()

    apparel_terms = [
        "shirt", "tshirt", "t-shirt", "tee", "sweatshirt",
        "hoodie", "apparel", "clothing", "top"
    ]

    search_keyword = keyword
    if not any(term in lower for term in apparel_terms):
        search_keyword = f"{keyword} shirt"

    return f"https://www.etsy.com/search?q={quote_plus(search_keyword)}"

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
                "Etsy検索語": term if any(x in term.lower() for x in ["shirt", "tshirt", "t-shirt", "tee", "sweatshirt", "hoodie"]) else f"{term} shirt",
                "Etsyで見る": etsy_search_url(term),
            })

    add_candidates(phrase_stats, "2語フレーズ")
    add_candidates(token_stats, "単語")

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows)

    # 需要テーマではなく、商品カテゴリ名・汎用語だけの候補を除外
    generic = {
        "japanese", "japan", "tokyo", "vintage", "retro", "streetwear",
        "shirt", "shirts", "tshirt", "tshirts", "tee", "tees",
        "hoodie", "hoodies", "sweatshirt", "sweatshirts",
        "jacket", "jackets", "coat", "coats", "sweater", "sweaters",
        "crewneck", "crewnecks", "pullover", "pullovers",
        "top", "tops", "apparel", "clothing",
        "graphic", "design", "style", "front", "back", "aesthetic"
    }
    result = result[~result["候補テーマ"].str.lower().isin(generic)]

    category_words = {
        "shirt", "shirts", "tshirt", "tshirts", "tee", "tees",
        "hoodie", "hoodies", "sweatshirt", "sweatshirts",
        "jacket", "jackets", "coat", "coats", "sweater", "sweaters",
        "crewneck", "crewnecks", "pullover", "pullovers",
        "top", "tops", "apparel", "clothing"
    }

    def has_real_theme(term: str) -> bool:
        parts = tokenize(term)
        meaningful = [
            p for p in parts
            if p not in category_words and p not in STOPWORDS and len(p) >= 3
        ]
        return bool(meaningful)

    result = result[result["候補テーマ"].apply(has_real_theme)]

    return result.sort_values(
        ["注目度", "合計閲覧数", "合計お気に入り数"],
        ascending=[False, False, False]
    ).drop_duplicates("候補テーマ").head(30)


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
st.caption("Etsyの公開商品データから、需要候補・売れ筋候補・テーマ別市場を調べます。")
st.info("🧩 バージョン：v13")

with st.expander("📘 使い方", expanded=False):
    st.markdown("""
**① 需要を探す**  
**Japan を必須軸**にして、アパレル内で反応が強いテーマ候補を探します。必要なら NINJA / Tokyo / Samurai などを追加できます。

**② 売れ筋を探す**  
「猫系」「キャンプ系」など大きなジャンルが決まっている時に、その中から細かい候補を探します。

**③ テーマを調べる**  
調べたいキーワードが決まっている時に、競合・価格・需要を比較します。

※ 実売上数そのものではなく、Views / Favorites / Favorite Rate / 競合状況からの推定です。
""")

tab1, tab2, tab3 = st.tabs(["🔥 需要を探す", "🔎 売れ筋を探す", "📊 テーマを調べる"])

with tab1:
    st.subheader("🔥 需要を探す")
    st.write(
        "アパレル内に限定して、反応が強いテーマ候補を自動で探します。"
        "必要なら Japan / NINJA などの追加キーワードも掛け合わせられます。"
    )

    apparel_choice = st.selectbox(
        "商品カテゴリ",
        ["Tシャツ", "パーカー", "スウェット", "全アパレル"],
        key="demand_apparel"
    )

    apparel_seed_map = {
        "Tシャツ": [
            "graphic tee", "funny shirt", "retro shirt", "vintage shirt",
            "cat shirt", "dog shirt", "camping shirt", "fishing shirt",
            "coffee shirt", "gaming shirt", "nurse shirt", "teacher shirt"
        ],
        "パーカー": [
            "graphic hoodie", "funny hoodie", "retro hoodie", "vintage hoodie",
            "cat hoodie", "dog hoodie", "camping hoodie", "fishing hoodie",
            "coffee hoodie", "gaming hoodie"
        ],
        "スウェット": [
            "graphic sweatshirt", "funny sweatshirt", "retro sweatshirt", "vintage sweatshirt",
            "cat sweatshirt", "dog sweatshirt", "camping sweatshirt", "fishing sweatshirt",
            "coffee sweatshirt", "gaming sweatshirt"
        ],
        "全アパレル": [
            "graphic shirt", "graphic tee", "graphic hoodie", "graphic sweatshirt",
            "funny shirt", "retro shirt", "vintage shirt", "cat shirt",
            "dog shirt", "camping shirt", "fishing shirt", "coffee shirt"
        ]
    }

    st.success("この事業では「日本」を軸にするため、検索には常に **Japan** を含めます。")

    extra_keyword = st.text_input(
        "追加キーワード（任意）",
        value="",
        placeholder="例：NINJA / Tokyo / Samurai / Ramen",
        help="Japan は常に自動で付きます。ここには追加したい切り口だけ入力してください。",
        key="demand_extra_keyword"
    ).strip()

    auto_seeds = apparel_seed_map[apparel_choice]

    # 「日本」を必須軸として常に Japan を付与。
    # 追加キーワードがあれば Japan + 追加語 + アパレル検索語 の形にする。
    if extra_keyword:
        auto_seeds = [
            f"Japan {extra_keyword} {seed}"
            for seed in auto_seeds
        ]
    else:
        auto_seeds = [
            f"Japan {seed}"
            for seed in auto_seeds
        ]

    c1, c2 = st.columns(2)
    with c1:
        st.metric("対象カテゴリ", apparel_choice)
    with c2:
        st.metric("追加キーワード", extra_keyword if extra_keyword else "なし")

    with st.expander("今回の検索キーワードを見る", expanded=False):
        st.write(", ".join(auto_seeds))

    st.info(
        "例：商品カテゴリを「Tシャツ」、追加キーワードを「NINJA」にすると、"
        "`Japan NINJA graphic tee`、`Japan NINJA retro shirt` などを横断して、"
        "『日本 × NINJA × Tシャツ』の需要候補を探します。"
    )

    st.caption("※ jacket / hoodie / shirt / back など、商品種別や意味の薄い語は需要候補から自動除外します。")

    if st.button("🔥 需要候補を自動で探す", type="primary", use_container_width=True, key="demand_search"):
        progress = st.progress(0)
        status = st.empty()
        all_listings, broad_summaries = [], []

        for i, seed in enumerate(auto_seeds, start=1):
            status.write(f"探索中：**{seed}**")
            try:
                df, summary = analyze_keyword(seed)
                if not df.empty:
                    all_listings.append(df)
                broad_summaries.append(summary)
            except Exception as e:
                st.error(f"{seed}：{e}")
            progress.progress(i / len(auto_seeds))

        progress.empty()
        status.empty()

        all_df = pd.concat(all_listings, ignore_index=True) if all_listings else pd.DataFrame()
        broad_summary_df = pd.DataFrame(broad_summaries)
        demand_candidates = extract_candidate_terms(all_df)

        if demand_candidates.empty:
            st.warning("需要候補を十分に抽出できませんでした。カテゴリや追加キーワードを変えて試してください。")
        else:
            demand_candidates = demand_candidates.copy()
            demand_candidates["需要スコア"] = (
                demand_candidates["注目度"] * 0.55
                + demand_candidates["お気に入り率(%)"].clip(upper=20) * 1.5
                + demand_candidates["出現商品数"].clip(upper=20) * 0.75
            ).clip(upper=100).round(1)

            demand_candidates = demand_candidates.sort_values(
                ["需要スコア", "合計閲覧数", "合計お気に入り数"],
                ascending=[False, False, False]
            )

            top = demand_candidates.iloc[0]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("需要候補", top["候補テーマ"])
            c2.metric("需要スコア", top["需要スコア"])
            c3.metric("合計閲覧数", int(top["合計閲覧数"]))
            c4.metric("お気に入り率", f'{top["お気に入り率(%)"]}%')

            st.subheader("需要候補 TOP15")
            show_cols = [
                "候補テーマ", "需要スコア", "出現商品数", "合計閲覧数",
                "合計お気に入り数", "お気に入り率(%)", "Etsy検索語", "Etsyで見る"
            ]
            st.dataframe(
                demand_candidates[show_cols].head(15),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Etsyで見る": st.column_config.LinkColumn(
                        "Etsyで見る",
                        display_text="Etsyで見る"
                    )
                }
            )

            st.caption(
                "追加キーワードを入れた場合は、その文脈を含む検索結果から需要候補を抽出しています。"
                "気になる候補はEtsy検索で実商品を確認してください。"
            )

            with st.expander("元の商品を見る", expanded=False):
                st.dataframe(
                    all_df.sort_values(["閲覧数", "お気に入り数"], ascending=[False, False]),
                    use_container_width=True,
                    hide_index=True,
                    column_config={"商品URL": st.column_config.LinkColumn("商品URL")}
                )

            excel_bytes = build_excel(broad_summary_df, all_df, demand_candidates)
            st.download_button(
                "⬇️ 需要発掘結果をExcelでダウンロード",
                data=excel_bytes,
                file_name="etsy_apparel_demand_discovery.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )

with tab2:
    st.subheader("🔎 売れ筋を探す")
    st.write("大きなジャンルの中から、反応の良い細かいテーマ候補を探します。")

    input_mode = st.radio(
        "入力方法",
        ["候補から選ぶ", "自分で入力する"],
        horizontal=True,
        key="trend_input_mode"
    )

    seed_map = {
        "日本系Tシャツ全般": ["japanese shirt", "japanese graphic tee", "funny japanese shirt", "japanese streetwear shirt"],
        "日本レトロ": ["japanese retro shirt", "retro japan shirt", "japanese vintage shirt", "japanese coffee shirt"],
        "東京・街": ["tokyo shirt", "tokyo train shirt", "tokyo nightlife shirt", "tokyo streetwear shirt"],
        "食・ラーメン": ["ramen shirt", "japanese food shirt", "sushi shirt", "japanese coffee shirt"],
        "猫・ペット": ["japanese cat shirt", "cat japan shirt", "japanese dog shirt", "kawaii cat shirt"],
    }

    if input_mode == "候補から選ぶ":
        seed_choice = st.selectbox("何系の商品を調べますか？", list(seed_map.keys()), key="trend_seed_choice")
        seeds = seed_map[seed_choice]
        st.caption("使用する親キーワード：" + ", ".join(seeds))
    else:
        st.info("細かい商品名ではなく広めのジャンルがおすすめです。例：camping shirt, fishing shirt, engineer shirt")
        seed_text = st.text_input(
            "調べたいジャンルを入力（カンマ区切り）",
            value="camping shirt, outdoor shirt, camper shirt",
            placeholder="例：camping shirt, fishing shirt, engineer shirt",
            key="trend_seed_text"
        )
        seeds = [x.strip() for x in seed_text.split(",") if x.strip()]

    if st.button("🔎 売れ筋候補を探す", type="primary", use_container_width=True, key="trend_search"):
        if not seeds:
            st.warning("ジャンルを入力してください。")
        else:
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

            progress.empty()
            status.empty()

            listings_df = pd.concat(all_listings, ignore_index=True) if all_listings else pd.DataFrame()
            summary_df = pd.DataFrame(seed_summaries)
            discovery_df = extract_candidate_terms(listings_df)

            if discovery_df.empty:
                st.warning("候補を十分に抽出できませんでした。")
            else:
                top = discovery_df.iloc[0]
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("注目候補", top["候補テーマ"])
                c2.metric("注目度", top["注目度"])
                c3.metric("関連商品数", int(top["出現商品数"]))
                c4.metric("お気に入り率", f'{top["お気に入り率(%)"]}%')

                st.subheader("注目テーマ候補 TOP15")
                st.dataframe(
                    discovery_df.head(15),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Etsyで見る": st.column_config.LinkColumn("Etsyで見る", display_text="Etsyで見る")
                    }
                )

                with st.expander("元の商品を見る", expanded=False):
                    st.dataframe(
                        listings_df.sort_values(["閲覧数", "お気に入り数"], ascending=[False, False]),
                        use_container_width=True,
                        hide_index=True,
                        column_config={"商品URL": st.column_config.LinkColumn("商品URL")}
                    )

                excel_bytes = build_excel(summary_df, listings_df, discovery_df)
                st.download_button(
                    "⬇️ 発掘結果をExcelでダウンロード",
                    data=excel_bytes,
                    file_name="etsy_trend_discovery.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )

with tab3:
    st.subheader("📊 テーマを調べる")
    st.write("調べたいキーワードが決まっている場合に、競合・価格・需要を比較します。")

    preset = st.selectbox(
        "候補",
        ["自由入力", "会社員・通勤", "東京・電車", "夜・終電", "日本レトロ"],
        key="compare_preset"
    )
    preset_values = {
        "自由入力": "",
        "会社員・通勤": "salaryman shirt, japanese office shirt, overtime shirt, commuter shirt",
        "東京・電車": "tokyo train shirt, tokyo commuter shirt, yamanote shirt, tokyo station shirt",
        "夜・終電": "last train shirt, tokyo nightlife shirt, late night tokyo shirt, tokyo last train shirt",
        "日本レトロ": "japanese retro shirt, kissaten shirt, retro japan shirt, japanese coffee shirt",
    }
    default_text = preset_values[preset] if preset != "自由入力" else "salaryman shirt, tokyo train shirt, japanese office shirt"

    keywords_text = st.text_input(
        "検索キーワード",
        value=default_text,
        placeholder="例：camping shirt, fishing shirt, engineer shirt",
        key="compare_keywords"
    )

    if st.button("📊 市場を分析する", type="primary", use_container_width=True, key="compare_search"):
        keywords = [x.strip() for x in keywords_text.split(",") if x.strip()]
        if not keywords:
            st.warning("検索キーワードを入力してください。")
        else:
            summaries, detail_frames = [], []
            progress = st.progress(0)
            status = st.empty()

            for i, keyword in enumerate(keywords, start=1):
                status.write(f"分析中：**{keyword}**")
                try:
                    detail_df, summary = analyze_keyword(keyword)
                    summaries.append(summary)
                    if not detail_df.empty:
                        detail_frames.append(detail_df)
                except Exception as e:
                    st.error(f"{keyword}：{e}")
                progress.progress(i / len(keywords))

            status.empty()
            progress.empty()

            if summaries:
                summary_df = pd.DataFrame(summaries).sort_values(
                    by=["参入期待度", "関連商品数"],
                    ascending=[False, False]
                )
                summary_df["Etsyで見る"] = summary_df["検索キーワード"].apply(etsy_search_url)

                listings_df = pd.concat(detail_frames, ignore_index=True) if detail_frames else pd.DataFrame()

                top = summary_df.iloc[0]
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("トップ候補", top["検索キーワード"])
                c2.metric("参入期待度", int(top["参入期待度"]))
                c3.metric("評価", top["評価"])
                c4.metric("関連商品数", int(top["関連商品数"]))

                visible_cols = [
                    "検索キーワード", "参入期待度", "評価", "Etsy検索件数",
                    "関連商品数", "関連商品率(%)", "価格中央値(USD)",
                    "関連商品の総閲覧数", "関連商品の総お気に入り数",
                    "お気に入り率(%)", "Etsyで見る"
                ]

                st.dataframe(
                    summary_df[visible_cols],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Etsyで見る": st.column_config.LinkColumn("Etsyで見る", display_text="Etsyで見る")
                    }
                )

                st.bar_chart(summary_df[["検索キーワード", "参入期待度"]].set_index("検索キーワード"))

                with st.expander("取得した商品を見る", expanded=False):
                    st.dataframe(
                        listings_df,
                        use_container_width=True,
                        hide_index=True,
                        column_config={"商品URL": st.column_config.LinkColumn("商品URL")}
                    )

                excel_bytes = build_excel(summary_df, listings_df)
                st.download_button(
                    "⬇️ 分析結果をExcelでダウンロード",
                    data=excel_bytes,
                    file_name="etsy_market_research.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )

st.divider()
st.caption("※ 需要候補・売れ筋候補・参入期待度はEtsyの公開データから算出した参考値です。実売上数そのものではありません。")
st.caption("The term 'Etsy' is a trademark of Etsy, Inc. This application uses the Etsy API but is not endorsed or certified by Etsy, Inc.")
