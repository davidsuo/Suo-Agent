# init_db.py
import sqlite3

conn = sqlite3.connect("sample.db")
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS employees (
        id INTEGER PRIMARY KEY,
        name TEXT,
        position TEXT,
        salary INTEGER
    )
''')

# 插入示例数据
sample_data = [
    (1, "张三", "工程师", 60000),
    (2, "李四", "产品经理", 75000),
    (3, "王五", "设计师", 55000),
    (4, "赵六", "数据分析师", 68000),
]
cursor.executemany("INSERT OR REPLACE INTO employees VALUES (?,?,?,?)", sample_data)
conn.commit()
conn.close()
print("数据库 sample.db 初始化完成，已插入示例员工数据。")