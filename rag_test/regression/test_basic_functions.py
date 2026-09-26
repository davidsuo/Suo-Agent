"""域 3：基本功能（记忆、kb/list、health）。"""
import httpx
import json


async def run(base_url: str):
    passed = failed = 0
    async with httpx.AsyncClient(timeout=120) as client:

        # ========== 用例 A：记忆 ==========
        session = "alice_回归记忆"
        await client.post(f"{base_url}/api/history/clear",
                          data={"session_id": session})
        await _chat(client, base_url, session, "我叫小明，记住这个名字")
        answer = await _chat(client, base_url, session, "我叫什么名字？")
        if "小明" in answer:
            passed += 1
            print(f"  [PASS] 记忆测试：答复包含'小明'")
        else:
            failed += 1
            print(f"  [FAIL] 记忆测试：答复未包含'小明' → {answer[:80]}")

        # ========== 用例 B：kb/list ==========
        try:
            r = await client.get(f"{base_url}/api/kb/list")
            if r.status_code == 200 and r.json().get("status") == "success":
                passed += 1
                print(f"  [PASS] /api/kb/list → {len(r.json().get('data', []))} 个文件")
            else:
                failed += 1
                print(f"  [FAIL] /api/kb/list 异常: {r.status_code}")
        except Exception as e:
            failed += 1
            print(f"  [ERR ] /api/kb/list: {e}")

        # ========== 用例 C：health ==========
        try:
            r = await client.get(f"{base_url}/api/health")
            if r.status_code == 200:
                passed += 1
                print(f"  [PASS] /api/health")
            else:
                failed += 1
                print(f"  [FAIL] /api/health HTTP {r.status_code}")
        except Exception as e:
            failed += 1
            print(f"  [ERR ] /api/health: {e}")

    return {"passed": passed, "failed": failed}


async def _chat(client, base_url, session_id, query):
    payload = {"session_id": session_id, "query": query, "user_text": query}
    answer = ""
    async with client.stream("POST", f"{base_url}/api/chat", json=payload) as r:
        async for line in r.aiter_lines():
            if line.startswith("data: "):
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    continue
                try:
                    data = json.loads(data_str)
                    if data.get("type") == "answer":
                        answer += data.get("content", "")
                except Exception:
                    pass
    return answer