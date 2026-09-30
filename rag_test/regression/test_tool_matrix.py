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