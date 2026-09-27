"""域 2：功能矩阵。"""
import httpx
import json

CASES = [
    # (id, username, query, expected_tools, session_suffix)
    # expected_tools 中的 "|" 表示"任一命中即通过"
    (1,  "alice", "明天上午9点提醒我开周会", ["add_event|list_events"], "t1"),
    (2,  "alice", "查看我明天的日程", ["list_events"], "t2"),
    (4,  "alice", "搜索今天科技新闻", ["web_search"], "t4"),
    (5,  "alice", "用 Python 计算 1024 除以 32", ["execute_python"], "t5"),
    (11, "alice", "查询员工工资", ["query_database"], "t11"),
    (16, "alice", "各月咖啡销售趋势并画出饼图", ["generate_chart"], "t16"),
    (17, "alice", "武汉今天的天气", ["web_search"], "t17"),
    # 权限拦截类：__FORBIDDEN__ 前缀表示"不能出现某工具"
    (101, "bob",   "查询员工工资", ["__FORBIDDEN__query_database"], "t101"),
    (102, "anan",  "搜索今天科技新闻", ["__FORBIDDEN__web_search"], "t102"),
    (103, "anan",  "明天上午9点提醒我开周会", ["__FORBIDDEN__add_event"], "t103"),
]


async def run(base_url: str):
    passed = failed = 0
    async with httpx.AsyncClient(timeout=180) as client:
        for case_id, username, query, expected_tools, suffix in CASES:
            session_id = f"{username}_回归_{suffix}"
            try:
                await client.post(f"{base_url}/api/history/clear",
                                  data={"session_id": session_id})
            except Exception:
                pass

            payload = {"session_id": session_id, "query": query, "user_text": query}
            tools_called = []
            try:
                async with client.stream("POST", f"{base_url}/api/chat", json=payload) as r:
                    if r.status_code != 200:
                        failed += 1
                        print(f"  [FAIL] #{case_id:3d} {username:8s} HTTP {r.status_code}")
                        continue
                    async for line in r.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            continue
                        try:
                            data = json.loads(data_str)
                            if data.get("type") == "tool":
                                content = data.get("content", "")
                                if ":" in content:
                                    t = content.split(":")[1].split("，")[0].strip()
                                    if t not in tools_called:
                                        tools_called.append(t)
                        except Exception:
                            pass

                # 解析 expected_tools 的语义
                forbidden = [t[12:] for t in expected_tools if t.startswith("__FORBIDDEN__")]
                required = [t for t in expected_tools if not t.startswith("__FORBIDDEN__")]

                if forbidden:
                    # 只要工具列表里没出现 forbidden 里任何一个，就算通过
                    actual = not any(f in tools_called for f in forbidden)
                elif not required:
                    # 期望不调任何工具
                    actual = len(tools_called) == 0
                else:
                    # 支持 "a|b|c" 任一命中
                    actual = all(
                        any(alt in tools_called for alt in t.split("|"))
                        for t in required
                    )

                result = "PASS" if actual else "FAIL"
                if result == "PASS":
                    passed += 1
                else:
                    failed += 1
                print(f"  [{result}] #{case_id:3d} {username:8s} "
                      f"期望={expected_tools} 实际={tools_called}")

            except Exception as e:
                failed += 1
                print(f"  [ERR ] #{case_id} {username}: {e}")

    return {"passed": passed, "failed": failed}