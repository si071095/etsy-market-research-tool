import io
import os
import re
import pandas as pd
import requests
import streamlit as st

API_BASE = "https://api.etsy.com/v3/application"
POSITIVE=["tokyo","japan","japanese","salaryman","commuter","station","subway","train","yamanote","office","overtime","work","corporate","night","late"]
NEGATIVE=["halloween","ghost","christian","church","retirement","clarksville","png","svg","digital download","template","clipart","sticker","mug","poster"]

def secret(name):
    v=os.getenv(name,"").strip()
    if not v:
        try:v=str(st.secrets.get(name,"")).strip()
        except Exception:v=""
    return v

def headers():
    k=secret("ETSY_KEYSTRING"); s=secret("ETSY_SHARED_SECRET")
    if not k or not s: raise RuntimeError("ETSY_KEYSTRING / ETSY_SHARED_SECRET が未設定です。")
    return {"x-api-key":f"{k}:{s}","Accept":"application/json"}

def money(m):
    if not m:return None
    try:return float(m.get("amount"))/float(m.get("divisor",100))
    except:return None

def rel_score(title,keyword):
    t=(title or "").lower(); k=(keyword or "").lower(); toks=re.findall(r"[a-z0-9]+",k); score=0
    if toks: score+=min(60,round(sum(1 for x in toks if x in t)/len(toks)*60))
    if k and k in t: score+=20
    score+=min(30,sum(1 for x in POSITIVE if x in t)*6)
    score-=min(50,sum(1 for x in NEGATIVE if x in t)*15)
    return max(0,min(100,score))

def band(s): return "High" if s>=80 else "Medium" if s>=50 else "Low"

def competition_score(n):
    return 45 if n<=50 else 42 if n<=100 else 37 if n<=250 else 32 if n<=500 else 25 if n<=1000 else 18 if n<=2000 else 10 if n<=5000 else 5 if n<=10000 else 2

def price_score(p):
    if p is None or pd.isna(p):return 0
    p=float(p)
    return 20 if 24<=p<=32 else 17 if (20<=p<24 or 32<p<=36) else 13 if (18<=p<20 or 36<p<=40) else 8 if (15<=p<18 or 40<p<=50) else 4

def demand_score(avg_f,max_f,rate,count):
    avg_f=0 if avg_f is None or pd.isna(avg_f) else float(avg_f); max_f=0 if max_f is None or pd.isna(max_f) else float(max_f); rate=0 if rate is None or pd.isna(rate) else float(rate)
    a=8 if avg_f>=50 else 6 if avg_f>=20 else 5 if avg_f>=10 else 3 if avg_f>=5 else 1 if avg_f>0 else 0
    m=5 if max_f>=1000 else 4 if max_f>=500 else 3 if max_f>=200 else 2 if max_f>=100 else 1 if max_f>0 else 0
    r=10 if rate>=10 else 8 if rate>=5 else 6 if rate>=3 else 4 if rate>=1.5 else 2 if rate>0 else 0
    rel=2 if count>=25 else 1 if count>=10 else 0
    return min(25,a+m+r+rel)

def sample_score(sample,total):
    if sample<=0:return 0
    base=7 if sample>=100 else 6 if sample>=75 else 5 if sample>=50 else 4 if sample>=25 else 3 if sample>=10 else 1
    cov=sample/total if total else 0; bonus=3 if cov>=.8 else 2 if cov>=.5 else 1 if cov>=.1 else 0
    return min(10,base+bonus)

def tiny_penalty(n): return -20 if n<=5 else -15 if n<=10 else -8 if n<=25 else -3 if n<=50 else 0
def saturation_penalty(n): return -20 if n>20000 else -15 if n>10000 else -10 if n>5000 else 0
def relevance_penalty(r): return 0 if r>=.6 else -5 if r>=.4 else -10 if r>=.2 else -15
def rank(s): return "S" if s>=80 else "A" if s>=65 else "B" if s>=50 else "C" if s>=35 else "D"

def search(keyword):
    r=requests.get(f"{API_BASE}/listings/active",headers=headers(),params={"keywords":keyword,"limit":100,"sort_on":"score","sort_order":"desc"},timeout=30)
    if r.status_code!=200: raise RuntimeError(f"Etsy API Error {r.status_code}: {r.text}")
    return r.json()

def analyze(keyword):
    payload=search(keyword); rows=[]
    for item in payload.get("results",[]):
        title=item.get("title") or ""; rs=rel_score(title,keyword); views=item.get("views"); fav=item.get("num_favorers")
        fr=None
        try:
            if views and fav is not None: fr=float(fav)/float(views)*100
        except: pass
        p=item.get("price") or {}
        rows.append({"keyword":keyword,"listing_id":item.get("listing_id"),"title":title,"price":money(p),"currency":p.get("currency_code"),"num_favorers":fav,"views":views,"favorite_rate_pct":round(fr,3) if fr is not None else None,"relevance_score":rs,"relevance_band":band(rs),"url":item.get("url"),"shop_id":item.get("shop_id")})
    df=pd.DataFrame(rows); total=int(payload.get("count",0) or 0)
    if df.empty:return df,{"keyword":keyword,"etsy_result_count":total,"sample_count":0,"high_relevance_count":0,"high_relevance_ratio_pct":0,"relevant_usd_median_price":None,"relevant_total_views":0,"relevant_total_favorites":0,"weighted_favorite_rate_pct":None,"competition_score":0,"price_score":0,"demand_score":0,"sample_score":0,"tiny_market_penalty":-20,"views_penalty":-10,"saturation_penalty":saturation_penalty(total),"relevance_penalty":-15,"opportunity_score":0,"opportunity_rank":"D"}
    high=df[df.relevance_band=="High"].copy(); ratio=len(high)/len(df) if len(df) else 0
    usd=high[high.currency=="USD"]; prices=pd.to_numeric(usd.price,errors="coerce").dropna(); med=round(prices.median(),2) if not prices.empty else None
    favs=pd.to_numeric(high.num_favorers,errors="coerce"); views=pd.to_numeric(high.views,errors="coerce"); total_f=int(favs.fillna(0).sum()) if not high.empty else 0; total_v=int(views.fillna(0).sum()) if not high.empty else 0
    wr=round(total_f/total_v*100,3) if total_v>0 else None; avg_f=round(favs.mean(),2) if favs.notna().any() else None; max_f=int(favs.max()) if favs.notna().any() else None
    comp=competition_score(len(high)); ps=price_score(med); ds=demand_score(avg_f,max_f,wr,len(high)); ss=sample_score(len(df),total); tp=tiny_penalty(len(high)); sp=saturation_penalty(total); rp=relevance_penalty(ratio); vp=-10 if total_v<20 else -5 if total_v<50 else 0
    opp=max(0,comp+ps+ds+ss+tp+sp+rp+vp)
    return df,{"keyword":keyword,"etsy_result_count":total,"sample_count":len(df),"high_relevance_count":len(high),"medium_relevance_count":int((df.relevance_band=="Medium").sum()),"low_relevance_count":int((df.relevance_band=="Low").sum()),"high_relevance_ratio_pct":round(ratio*100,2),"relevant_usd_median_price":med,"relevant_avg_favorites":avg_f,"relevant_max_favorites":max_f,"relevant_total_views":total_v,"relevant_total_favorites":total_f,"weighted_favorite_rate_pct":wr,"competition_score":comp,"price_score":ps,"demand_score":ds,"sample_score":ss,"tiny_market_penalty":tp,"views_penalty":vp,"saturation_penalty":sp,"relevance_penalty":rp,"opportunity_score":opp,"opportunity_rank":rank(opp)}

def to_excel(summary,listings):
    b=io.BytesIO()
    with pd.ExcelWriter(b,engine="openpyxl") as w:
        summary.to_excel(w,sheet_name="Summary",index=False); listings.to_excel(w,sheet_name="Listings",index=False)
    return b.getvalue()

st.set_page_config(page_title="Marketplace Research Tool",page_icon="📊",layout="wide")
st.title("📊 Marketplace Research Tool")
st.caption("Etsy公開商品データから、POD向け市場候補を比較します。")
kw=st.text_area("検索キーワード",value="salaryman shirt, tokyo train shirt, last train shirt, japanese office shirt",height=100,help="複数の場合はカンマ区切り")
if st.button("🔎 分析開始",type="primary",use_container_width=True):
    kws=[x.strip() for x in kw.split(",") if x.strip()]
    if not kws: st.warning("検索キーワードを入力してください。"); st.stop()
    sums=[]; frames=[]; prog=st.progress(0); status=st.empty()
    for i,k in enumerate(kws,1):
        status.write(f"分析中: **{k}**")
        try:
            d,s=analyze(k); sums.append(s); frames.append(d) if not d.empty else None
        except Exception as e: st.error(f"{k}: {e}")
        prog.progress(i/len(kws))
    status.empty(); prog.empty()
    if not sums: st.stop()
    sdf=pd.DataFrame(sums).sort_values(["opportunity_score","high_relevance_count"],ascending=[False,False]); ddf=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
    st.success("分析完了")
    top=sdf.iloc[0]; c1,c2,c3,c4=st.columns(4); c1.metric("トップ候補",top.keyword); c2.metric("Opportunity Score",int(top.opportunity_score)); c3.metric("Rank",top.opportunity_rank); c4.metric("High関連商品",int(top.high_relevance_count))
    cols=["keyword","opportunity_score","opportunity_rank","etsy_result_count","high_relevance_count","high_relevance_ratio_pct","relevant_usd_median_price","relevant_total_views","relevant_total_favorites","weighted_favorite_rate_pct"]
    st.subheader("市場比較"); st.dataframe(sdf[cols],use_container_width=True,hide_index=True); st.bar_chart(sdf[["keyword","opportunity_score"]].set_index("keyword"))
    with st.expander("商品一覧を見る"):
        st.dataframe(ddf,use_container_width=True,hide_index=True,column_config={"url":st.column_config.LinkColumn("Etsy URL")})
    st.download_button("⬇️ Excelをダウンロード",data=to_excel(sdf,ddf),file_name="etsy_market_research.xlsx",mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",use_container_width=True)
st.divider(); st.caption("The term 'Etsy' is a trademark of Etsy, Inc. This application uses the Etsy API but is not endorsed or certified by Etsy, Inc.")
