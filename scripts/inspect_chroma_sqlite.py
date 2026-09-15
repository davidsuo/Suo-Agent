# scripts/inspect_chroma_sqlite.py
import os
import sqlite3

db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chroma_db", "chroma.sqlite3")
print(f"DB 路径: {db_path}")
print(f"文件大小: {os.path.getsize(db_path)} bytes\n")

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 列出所有表
print("=== 表列表 ===")
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cursor.fetchall()]
for t in tables:
    print(f"  {t}")

print("\n=== collections 表 ===")
try:
    cursor.execute("SELECT id, name FROM collections")
    rows = cursor.fetchall()
    for r in rows:
        print(f"  id={r[0]}, name={r[1]}")
    if not rows:
        print("  (空)")
except Exception as e:
    print(f"  查询失败: {e}")

print("\n=== 是否有 embeddings 表 ===")
try:
    cursor.execute("SELECT COUNT(*) FROM embeddings")
    print(f"  embeddings 行数: {cursor.fetchone()[0]}")
except Exception as e:
    print(f"  查询失败: {e}")

print("\n=== segments 表（ChromaDB 新版用这个存 collection） ===")
try:
    cursor.execute("SELECT id, type, scope FROM segments LIMIT 20")
    for r in cursor.fetchall():
        print(f"  id={r[0][:16]}..., type={r[1]}, scope={r[2]}")
except Exception as e:
    print(f"  查询失败: {e}")

conn.close()