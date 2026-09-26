"""域 1：角色访问权限。"""
import httpx

USERS = {
    "carol": "admin",
    "alice": "manager",
    "bob":   "developer",
    "lucky": "manager",
    "anan":  "viewer",
}

MATRIX = [
    ("/api/logs", "json", {"admin": True, "manager": True, "developer": True, "viewer": False}),
    ("/api/logs/export", "csv", {"admin": True, "manager": True, "developer": True, "viewer": False}),
]


async def run(base_url: str):
    passed = failed = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for endpoint, resp_type, role_map in MATRIX:
            for username, role in USERS.items():
                expected = role_map.get(role, True)
                session_id = f"{username}_主对话"
                try:
                    r = await client.get(f"{base_url}{endpoint}",
                                         params={"session_id": session_id})

                    # actual_allowed 统一语义：True=允许，False=拒绝
                    if resp_type == "json":
                        try:
                            body = r.json()
                            actual_allowed = body.get("status") == "success"
                        except Exception:
                            actual_allowed = False
                    else:
                        content_type = r.headers.get("content-type", "")
                        actual_allowed = "text/csv" in content_type

                    # 断言：期望允许 = 实际允许；期望拒绝 = 实际拒绝
                    result = "PASS" if actual_allowed == expected else "FAIL"
                    if result == "PASS":
                        passed += 1
                    else:
                        failed += 1
                    print(f"  [{result}] {username}({role:9s}) {endpoint:20s} "
                          f"期望{'允许' if expected else '拒绝'} 实际{'允许' if actual_allowed else '拒绝'}")
                except Exception as e:
                    failed += 1
                    print(f"  [ERR ] {username} {endpoint}: {e}")
    return {"passed": passed, "failed": failed}