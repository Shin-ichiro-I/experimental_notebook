# main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import notes

# FastAPIアプリケーションのインスタンスを作成
app = FastAPI(
    title="Graph Note API",
    description="API for retrieving graph data from notes.",
    version="1.0.0",
)

# --- ▼▼▼ CORSミドルウェアの設定をここに追加 ▼▼▼ ---
# これにより、全てのルーターにCORS設定が適用されます
origins = [
    "null",  # ローカルファイル (file://) からのアクセスを許可
    "http://localhost",
    "http://localhost:8080",
]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"], # すべてのHTTPメソッドを許可
    allow_headers=["*"], # すべてのHTTPヘッダーを許可
)
# --- ▲▲▲▲▲ CORSミドルウェアの設定をここに追加 ▲▲▲▲▲ ---

# routers/notes.pyで定義したルーターをアプリケーションに含める
app.include_router(
    notes.router,
    prefix="/api/v1",  # 全てのエンドポイントの先頭に /api/v1 を付与
    tags=["Notes"]     # APIドキュメントで "Notes" としてグループ化
)

@app.get("/", tags="ROOT")
async def read_root():
    """APIのルートエンドポイント"""
    return {"message": "Welcome to the Graph Note API!"}