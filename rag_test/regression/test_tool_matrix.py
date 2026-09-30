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
    (102, "anna",  "搜索今天科技新闻", ["__FORBIDDEN__web_search"], "t102"),
    (103, "anna",  "明天上午9点提醒我开周会", ["__FORBIDDEN__add_event"], "t103"),
]


async def run(base_url: str):
    passed = failed = 0
    async with httpx.AsyncClient(timeout=180) as client:

        async def _run_once(username, query, suffix):
            """执行一次，返回 tools_called；HTTP 错误返回 None。"""
            session_id = f"{username}_回归_{suffix}"
            try:
                await client.post(f"{base_url}/api/history/clear",
                                  data={"session_id": session_id})
            except Exception:
                pass
            payload = {"session_id": session_id, "query": query, "user_text": query}
            tools_called = []
            async with client.stream("POST", f"{base_url}/api/chat", json=payload) as r:
                if r.status_code != 200:
                    return None
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
            return tools_called

        def _judge(tools_called, forbidden, required):
            if forbidden:
                return not any(f in tools_called for f in forbidden)
            if not required:
                return len(tools_called) == 0
            return all(
                any(alt in tools_called for alt in t.split("|"))
                for t in required
            )

        for case_id, username, query, expected_tools, suffix in CASES:
            forbidden = [t[12:] for t in expected_tools if t.startswith("__FORBIDDEN__")]
            required = [t for t in expected_tools if not t.startswith("__FORBIDDEN__")]

            # 最多尝试 2 次（首次 + 1 次重试），任一通过即 PASS
            result = "FAIL"
            tools_called = []
            for attempt in range(2):
                try:
                    tools_called = await _run_once(username, query, suffix)
                    if tools_called is None:
                        result = "FAIL"
                        continue
                    if _judge(tools_called, forbidden, required):
                        result = "PASS"
                        break
                    else:
                        result = "FAIL"
                except Exception as e:
                    print(f"  [ERR ] #{case_id} {username} 第 {attempt+1} 次: {e}")
                    result = "FAIL"

            if result == "PASS":
                passed += 1
            else:
                failed += 1
            print(f"  [{result}] #{case_id:3d} {username:8s} "
                  f"期望={expected_tools} 实际={tools_called}")

    return {"passed": passed, "failed": failed}