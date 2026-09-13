# test/run_eval.py
import json, subprocess, sys

with open("test/eval_dataset.json", "r", encoding="utf-8") as f:
    dataset = json.load(f)

print("=== 开始执行回归评估 ===")
for case in dataset:
    # 这里建议接入你后端的 API 或直接调用 chat_core 进行测试
    # 观察日志中的 tool_trace 是否符合 expected_tool
    print(f"测试用例: {case['query']} | 期望工具: {case['expected_tool']}")
    # TODO: 编写调用逻辑，并断言是否有幻觉
print("=== 回归评估完成 ===")