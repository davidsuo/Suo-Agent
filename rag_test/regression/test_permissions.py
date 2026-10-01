"""域 1：角色访问权限（角色驱动，从后端拉用户列表）。"""
import httpx

# 期望矩阵：角色 → endpoint 是否允许
ROLE_MATRIX = {
    "/api/logs":        {"admin": True, "manager": True, "developer": True, "viewer": False},
    "/api/logs/export": {"admin": True, "manager": True, "developer": True, "viewer": False},
}

# endpoint 响应类型
RESP_TYPES = {
    "/api/logs": "json",
    "/api/logs/export": "csv",
}


async def fetch_users(client, base_url):
    """从后端拉取 username → role 映射"""
    r = await client.get(f"{base_url}/api/test/user-roles")
    if r.status_code != 200:
        raise RuntimeError(f"无法获取用户列表: HTTP {r.status_code}")
    body = r.json()
    if body.get("status") != "success":
        raise RuntimeError(f"无法获取用户列表: {body}")
    return body["data"]


async def run(base_url: str):
    passed = failed = 0
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            users = await fetch_users(client, base_url)
            print(f"  [INFO] 从后端拉取 {len(users)} 个用户：{list(users.keys())}")
        except Exception as e:
            print(f"  [ERR ] 无法拉取用户列表：{e}")
            return {"passed": 0, "failed": 1}

        for endpoint, role_map in ROLE_MATRIX.items():
            resp_type = RESP_TYPES[endpoint]
            for username, role in users.items():
                expected = role_map.get(role, True)
                session_id = f"{username}_主对话"
                try:
                    r = await client.get(f"{base_url}{endpoint}",
                                         params={"session_id": session_id})

                    if resp_type == "json":
                        try:
                            body = r.json()
                            actual_allowed = body.get("status") == "success"
                        except Exception:
                            actual_allowed = False
                    else:
                        content_type = r.headers.get("content-type", "")
                        actual_allowed = "text/csv" in content_type

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
                    

        # ========== US-06：越权边界测试（无 session_id / 空 / 不存在用户） ==========
        print(f"\n  --- US-06: 越权边界 ---")

        # 1. 不带 session_id 访问 /api/logs → 应被拒
        try:
            r = await client.get(f"{base_url}/api/logs")
            body = r.json()
            if body.get("status") == "error" and "session_id" in body.get("message", ""):
                passed += 1
                print(f"  [PASS] /api/logs 无 session_id 被拒")
            else:
                failed += 1
                print(f"  [FAIL] /api/logs 无 session_id 未被拒 → {body}")
        except Exception as e:
            failed += 1
            print(f"  [ERR ] /api/logs 无 session_id: {e}")

        # 2. 不带 session_id 访问 /api/logs/export → 应被拒（返回 JSON 而非 CSV）
        try:
            r = await client.get(f"{base_url}/api/logs/export")
            content_type = r.headers.get("content-type", "")
            if "text/csv" in content_type:
                failed += 1
                print(f"  [FAIL] /api/logs/export 无 session_id 未被拒（返回了 CSV）")
            else:
                passed += 1
                print(f"  [PASS] /api/logs/export 无 session_id 被拒（content_type={content_type}）")
        except Exception as e:
            failed += 1
            print(f"  [ERR ] /api/logs/export 无 session_id: {e}")

        # 3. 空字符串 session_id 访问 /api/logs → 应被拒
        try:
            r = await client.get(f"{base_url}/api/logs", params={"session_id": ""})
            body = r.json()
            if body.get("status") == "error":
                passed += 1
                print(f"  [PASS] /api/logs 空 session_id 被拒")
            else:
                failed += 1
                print(f"  [FAIL] /api/logs 空 session_id 未被拒 → {body}")
        except Exception as e:
            failed += 1
            print(f"  [ERR ] /api/logs 空 session_id: {e}")

        # 4. 不存在的用户 → 应被拒
        try:
            r = await client.get(f"{base_url}/api/logs", params={"session_id": "notexist_主对话"})
            body = r.json()
            if body.get("status") == "error":
                passed += 1
                print(f"  [PASS] /api/logs 不存在用户被拒")
            else:
                failed += 1
                print(f"  [FAIL] /api/logs 不存在用户未被拒 → {body}")
        except Exception as e:
            failed += 1
            print(f"  [ERR ] /api/logs 不存在用户: {e}")


        # ========== US-07：/api/users/roles 端点可用性 ==========
        print(f"\n  --- US-07: 角色列表端点 ---")
        try:
            r = await client.get(f"{base_url}/api/users/roles")
            body = r.json()
            roles = body.get("data", [])
            if body.get("status") == "success" and len(roles) >= 4:
                values = [x.get("value") for x in roles]
                if all(k in values for k in ["admin", "manager", "developer", "viewer"]):
                    passed += 1
                    print(f"  [PASS] /api/users/roles 返回 {len(roles)} 个角色: {values}")
                else:
                    failed += 1
                    print(f"  [FAIL] /api/users/roles 缺少标准角色 → {values}")
            else:
                failed += 1
                print(f"  [FAIL] /api/users/roles 响应异常 → {body}")
        except Exception as e:
            failed += 1
            print(f"  [ERR ] /api/users/roles: {e}")


        # ========== 附加测试：/api/logs/export 的 CSV 内容 ==========
        print(f"\n  --- 附加：/api/logs/export CSV 内容校验 ---")
        try:
            r = await client.get(f"{base_url}/api/logs/export",
                                 params={"session_id": "alice_主对话"})
            if r.status_code == 200 and "text/csv" in r.headers.get("content-type", ""):
                csv_text = r.text
                # 断言：CSV 应该包含表头
                if "时间戳" in csv_text and "操作人" in csv_text:
                    passed += 1
                    print(f"  [PASS] /api/logs/export CSV 头正确（{len(csv_text)} 字符）")
                else:
                    failed += 1
                    print(f"  [FAIL] /api/logs/export CSV 头缺失 → {csv_text[:100]}")
            else:
                failed += 1
                print(f"  [FAIL] /api/logs/export 返回异常")
        except Exception as e:
            failed += 1
            print(f"  [ERR ] /api/logs/export: {e}")
            
    return {"passed": passed, "failed": failed}