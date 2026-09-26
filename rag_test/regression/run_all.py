"""
RAG 系统回归测试总入口。

用法：
    python rag_test/regression/run_all.py            # 本地
    python rag_test/regression/run_all.py --cloud    # 云端
    python rag_test/regression/run_all.py --only permissions
"""
import asyncio
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_permissions
import test_tool_matrix
import test_basic_functions


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cloud", action="store_true")
    parser.add_argument("--only", choices=["permissions", "tool_matrix", "basic"])
    args = parser.parse_args()

    base_url = "https://suo-agent.onrender.com" if args.cloud else "http://127.0.0.1:10000"
    print(f"\n{'='*70}\nRAG 回归测试 | 目标: {base_url}\n{'='*70}\n")

    results = {}

    if not args.only or args.only == "permissions":
        print("\n▶▶▶ 域 1：角色访问权限\n")
        results["permissions"] = await test_permissions.run(base_url)

    if not args.only or args.only == "tool_matrix":
        print("\n▶▶▶ 域 2：19 条功能矩阵\n")
        results["tool_matrix"] = await test_tool_matrix.run(base_url)

    if not args.only or args.only == "basic":
        print("\n▶▶▶ 域 3：基本功能\n")
        results["basic"] = await test_basic_functions.run(base_url)

    # 汇总
    print(f"\n{'='*70}\n汇总\n{'='*70}")
    tp = tf = 0
    for name, r in results.items():
        print(f"  {name:15s}  通过 {r['passed']:3d} / 失败 {r['failed']:3d}")
        tp += r["passed"]
        tf += r["failed"]
    print(f"{'-'*70}\n  合计              通过 {tp:3d} / 失败 {tf:3d}\n{'='*70}\n")
    sys.exit(0 if tf == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())