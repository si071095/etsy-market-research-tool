# Marketplace Research Tool - Web版

## ローカル
`pip install -r requirements.txt`

環境変数を設定後、`streamlit run app.py`

## Render
GitHubへアップロード → RenderのWeb Serviceで接続。
Environment Variablesに `ETSY_KEYSTRING` と `ETSY_SHARED_SECRET` を設定。

Build: `pip install -r requirements.txt`
Start: `streamlit run app.py --server.port $PORT --server.address 0.0.0.0`
