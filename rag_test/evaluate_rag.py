# rag_test/evaluate_rag.py
import sys, os, json

# 定位项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

RAG_DATA_FILE = os.path.join(BASE_DIR, "rag_data.json")
TEST_DIR = os.path.dirname(os.path.abspath(__file__))

# 硬编码20条测试数据
TEST_CASES = [
    {"id": "Q01", "question": "打印机报错 0x80004005 怎么处理", "type": "exact", "ground_truth": ["IT-01"]},
    {"id": "Q02", "question": "打印的东西一直出不来，点了打印没有反应，该怎么办", "type": "semantic", "ground_truth": ["IT-02"]},
    {"id": "Q03", "question": "办公室的 HP 打印机今天突然显示脱机，任务全卡着不动，怎么恢复", "type": "hybrid", "ground_truth": ["IT-07", "IT-02"]},
    {"id": "Q04", "question": "共享打印机提示拒绝访问，权限在哪里设置", "type": "exact", "ground_truth": ["IT-05"]},
    {"id": "Q05", "question": "电脑刚重装完系统，打印机就失踪了，别人那台都用得好好的", "type": "semantic", "ground_truth": ["IT-04"]},
    {"id": "Q06", "question": "取出卡住的纸之后，打印机还是老毛病，一打印就报错", "type": "hybrid", "ground_truth": ["IT-06", "IT-01"]},
    {"id": "Q07", "question": "打印出来颜色很淡还有条纹，是不是该换耗材了", "type": "exact", "ground_truth": ["IT-08"]},
    {"id": "Q08", "question": "系统前几天自动升级完，打印机就变得很不稳定，经常打一半中断", "type": "hybrid", "ground_truth": ["IT-04", "IT-02"]},
    {"id": "Q09", "question": "公司下午茶的报销流程是怎样的", "type": "negative", "ground_truth": []},
    {"id": "Q10", "question": "打印机端口 9100 连不上，防火墙怎么放行", "type": "exact", "ground_truth": ["IT-03"]},
    {"id": "Q11", "question": "ThinkPad X1 Carbon 2024 开机没反应，电源灯不亮", "type": "exact", "ground_truth": ["TS-01"]},
    {"id": "Q12", "question": "开机后一直没画面，能听到机器运转的声音，屏幕就是没图像", "type": "semantic", "ground_truth": ["TS-02"]},
    {"id": "Q13", "question": "我的 X1 2024 开机后屏幕一片漆黑，机器在转但啥都不显示", "type": "hybrid", "ground_truth": ["TS-01", "TS-02"]},
    {"id": "Q14", "question": "开机蓝屏 0x0000007B 是什么问题", "type": "exact", "ground_truth": ["TS-05"]},
    {"id": "Q15", "question": "电源插着也一直掉电，拿掉适配器更撑不了几分钟", "type": "semantic", "ground_truth": ["TS-04"]},
    {"id": "Q16", "question": "X1 用着用着就突然重启，散热口附近特别热，风扇跟拖拉机一样响", "type": "hybrid", "ground_truth": ["TS-06", "TS-05"]},
    {"id": "Q17", "question": "家里网连不上，手机都能上，就电脑不行", "type": "semantic", "ground_truth": ["TS-07"]},
    {"id": "Q18", "question": "外接显示器没信号，按 F7 也没用", "type": "exact", "ground_truth": ["TS-08"]},
    {"id": "Q19", "question": "这台电脑的保修期到什么时候，发票在哪里开", "type": "negative", "ground_truth": []},
    {"id": "Q20", "question": "电脑最近老是突然自己重启，没有任何提示", "type": "semantic", "ground_truth": ["TS-05", "TS-06"]}
]

def evaluate(mode):
    try:
        with open(RAG_DATA_FILE, "r", encoding="utf-8") as f:
            store = json.load(f)
        all_texts = []
        for key in store.get("store", {}).keys():
            all_texts.extend([doc.get("text", "") for doc in store["store"][key]])
        all_text = "\n".join(all_texts)
    except Exception as e:
        print(f"❌ 读取 rag_data.json 失败：{e}", flush=True)
        return

    total, hits = 0, 0
    print(f"\n===== 开始真实评估模式: {mode} =====", flush=True)

    # 只有在当前模式是 v1 或 v2 时才导入模块（避免顶层导入导致模型加载卡死）
    if mode == "v1":
        try:
            from common.rag import search_knowledge as search_func
        except Exception as e:
            print(f"❌ V1 模块导入失败：{e}", flush=True)
            return
    elif mode == "v2":
        try:
            from common.rag_v2 import search_knowledge_v2 as search_func
            print("⏳ 正在加载向量模型，首次可能需要几秒，请耐心等待...", flush=True)
        except Exception as e:
            print(f"❌ V2 模块导入失败：{e}", flush=True)
            return

    for case in TEST_CASES:
        q = case["question"]
        gt = case["ground_truth"]
        total += 1
        result = ""
        try:
            # 如果要在真实 V2 语义检索上进行测试，这里使用全量
            if mode == "v1":
                result = search_func(q, "eval_session", "")
            elif mode == "v2":
                result = search_func(q, "")
        except Exception as e:
            result = ""

        doc_hit = any(doc in result for doc in gt)
        if not gt and not result:
            doc_hit = True
        elif not gt and result:
            doc_hit = False

        if doc_hit:
            hits += 1
            print(f"✅ [通过] {q}", flush=True)
        else:
            print(f"❌ [失败] {q} (期望: {gt})", flush=True)

    print(f"\n=> {mode} HitRate: {hits}/{total} = {hits/total*100:.1f}%", flush=True)

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ["v1", "all"]:
        evaluate("v1")
    if mode in ["v2", "all"]:
        evaluate("v2")
    print("\n完成！", flush=True)