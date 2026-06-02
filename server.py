"""
Word Match 云端排行榜服务器
FastAPI + SQLite，支持跨设备分数同步

本地运行: python server.py
部署: 可部署到 Render / Railway / 任意支持 Python 的云服务
"""

import os
import json
from datetime import datetime, date
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(title="Word Match Leaderboard")

# 允许所有来源访问（生产环境可限制为你的域名）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# SQLite 数据库（无需额外安装，Python 内置）
# ============================================================
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "wordmatch_cloud.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_name TEXT NOT NULL,
            mode TEXT NOT NULL,
            score INTEGER NOT NULL,
            level INTEGER NOT NULL,
            max_combo INTEGER NOT NULL,
            words_done INTEGER DEFAULT 0,
            play_seconds INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS players (
            player_name TEXT PRIMARY KEY,
            total_games INTEGER DEFAULT 0,
            total_score INTEGER DEFAULT 0,
            best_combo INTEGER DEFAULT 0,
            highest_level INTEGER DEFAULT 0,
            total_words INTEGER DEFAULT 0,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 索引
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scores_mode ON scores(mode)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scores_player ON scores(player_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scores_score ON scores(score DESC)")
    conn.commit()
    conn.close()


@app.on_event("startup")
def startup():
    init_db()
    print(f"[服务器] 数据库已初始化: {DB_PATH}")


# ============================================================
# API 路由
# ============================================================

@app.post("/api/submit")
def submit_score(data: dict):
    """提交一条游戏记录"""
    player_name = (data.get("name") or "玩家").strip()[:20]
    mode = data.get("mode", "timed")
    score = int(data.get("score", 0))
    level = int(data.get("level", 1))
    max_combo = int(data.get("combo", 0))
    words_done = int(data.get("words_done", 0))
    play_seconds = int(data.get("play_seconds", 0))

    if mode not in ("timed", "practice", "endless"):
        raise HTTPException(400, "无效的游戏模式")

    if score < 0:
        raise HTTPException(400, "分数不能为负数")

    conn = get_db()
    try:
        # 插入分数记录
        conn.execute(
            "INSERT INTO scores (player_name, mode, score, level, max_combo, words_done, play_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (player_name, mode, score, level, max_combo, words_done, play_seconds)
        )

        # 更新玩家统计
        existing = conn.execute(
            "SELECT * FROM players WHERE player_name = ?", (player_name,)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE players SET total_games = total_games + 1, "
                "total_score = total_score + ?, "
                "best_combo = MAX(best_combo, ?), "
                "highest_level = MAX(highest_level, ?), "
                "total_words = total_words + ?, "
                "last_seen = CURRENT_TIMESTAMP "
                "WHERE player_name = ?",
                (score, max_combo, level, words_done, player_name)
            )
        else:
            conn.execute(
                "INSERT INTO players (player_name, total_games, total_score, best_combo, "
                "highest_level, total_words) VALUES (?, 1, ?, ?, ?, ?)",
                (player_name, score, max_combo, level, words_done)
            )

        conn.commit()

        # 获取该模式下的排名
        rank_row = conn.execute(
            "SELECT COUNT(*) + 1 as rank FROM scores "
            "WHERE mode = ? AND score > ?",
            (mode, score)
        ).fetchone()
        rank = rank_row["rank"] if rank_row else 1

        return {
            "success": True,
            "rank": rank,
            "message": f"成绩已上传！全球排名第 {rank} 名"
        }
    finally:
        conn.close()


@app.get("/api/leaderboard/{mode}")
def get_leaderboard(mode: str, limit: int = 20):
    """获取某个模式的全球排行榜"""
    if mode not in ("timed", "practice", "endless"):
        raise HTTPException(400, "无效的游戏模式")

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT player_name, score, level, max_combo, words_done, play_seconds, created_at "
            "FROM scores WHERE mode = ? "
            "ORDER BY score DESC LIMIT ?",
            (mode, limit)
        ).fetchall()

        leaderboard = []
        for i, row in enumerate(rows):
            leaderboard.append({
                "rank": i + 1,
                "name": row["player_name"],
                "score": row["score"],
                "level": row["level"],
                "combo": row["max_combo"],
                "words_done": row["words_done"],
                "date": row["created_at"][:10] if row["created_at"] else "",
            })

        return {"mode": mode, "leaderboard": leaderboard}
    finally:
        conn.close()


@app.get("/api/players")
def get_players():
    """获取所有玩家的学习进度（按记忆点数排序）"""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT player_name, total_games, total_score, best_combo, "
            "highest_level, total_words, last_seen "
            "FROM players ORDER BY total_score DESC LIMIT 50"
        ).fetchall()

        players = []
        for row in rows:
            players.append({
                "name": row["player_name"],
                "games": row["total_games"],
                "total_score": row["total_score"],
                "best_combo": row["best_combo"],
                "highest_level": row["highest_level"],
                "total_words": row["total_words"],
                "last_seen": row["last_seen"][:10] if row["last_seen"] else "",
            })

        return {"players": players}
    finally:
        conn.close()


@app.get("/api/player/{name}")
def get_player(name: str):
    """获取单个玩家的详细统计"""
    conn = get_db()
    try:
        player = conn.execute(
            "SELECT * FROM players WHERE player_name = ?", (name,)
        ).fetchone()

        if not player:
            raise HTTPException(404, "玩家不存在")

        # 获取该玩家各模式的最高分
        mode_scores = {}
        for mode in ("timed", "practice", "endless"):
            row = conn.execute(
                "SELECT MAX(score) as high FROM scores WHERE player_name = ? AND mode = ?",
                (name, mode)
            ).fetchone()
            mode_scores[mode] = row["high"] if row and row["high"] else 0

        return {
            "name": player["player_name"],
            "games": player["total_games"],
            "total_score": player["total_score"],
            "best_combo": player["best_combo"],
            "highest_level": player["highest_level"],
            "total_words": player["total_words"],
            "high_scores": mode_scores,
            "last_seen": player["last_seen"][:10] if player["last_seen"] else "",
        }
    finally:
        conn.close()


@app.get("/")
def root():
    return {
        "name": "Word Match 云端排行榜 API",
        "version": "1.0",
        "endpoints": [
            "POST /api/submit - 提交分数",
            "GET /api/leaderboard/{mode} - 获取排行榜",
            "GET /api/players - 获取所有玩家",
            "GET /api/player/{name} - 获取玩家详情",
        ]
    }


# ============================================================
# 启动
# ============================================================
if __name__ == "__main__":
    print("=" * 50)
    print("  Word Match 云端排行榜服务器")
    print("  访问 http://localhost:8765 查看 API 文档")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8765)
