# -*- coding: utf-8 -*-
"""验证：knowledge_base.md 与 test_set.json 的配套性 + 稀疏检索召回能力
不依赖后端，直接对 20 篇知识库文档做 BM25(字符bigram) 检索，对比 ground_truth
"""
import sys, os, re, json, math
from collections import Counter
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(BASE, 'knowledge_base.md')
TS = os.path.join(BASE, 'test_set.json')

def parse_kb(path):
    docs = {}
    cur_id, cur_title, buf = None, None, []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            m = re.match(r'##\s*\[([A-Z]+-\d+)\]\s*(.*)', line.strip())
            if m:
                if cur_id:
                    docs[cur_id] = (cur_title, ' '.join(buf))
                cur_id, cur_title = m.group(1), m.group(2)
                buf = []
            elif cur_id:
                buf.append(line.strip())
        if cur_id:
            docs[cur_id] = (cur_title, ' '.join(buf))
    return docs

def tokenize(text):
    text = text.lower()
    # 中文按字符 bigram + 保留英文数字串
    tokens = []
    for seg in re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]+', text):
        if re.fullmatch(r'[a-z0-9]+', seg):
            tokens.append(seg)
        else:
            tokens += [seg[i:i+2] for i in range(max(len(seg)-1, 0))]
    return tokens

def bm25(docs, query, k1=1.5, b=0.75, top=5):
    N = len(docs)
    avgdl = sum(len(v[0]) + len(v[1]) for v in docs.values()) / N if N else 1
    dfs = Counter()
    for _, (t, c) in docs.items():
        for w in set(tokenize(t + c)):
            dfs[w] += 1
    scores = {}
    for did, (t, c) in docs.items():
        dl = len(tokenize(t + c))
        tf = Counter(tokenize(t + c))
        s = 0.0
        for q in set(tokenize(query)):
            if q not in tf: continue
            n = dfs[q]
            idf = math.log(1 + (N - n + 0.5) / (n + 0.5))
            s += idf * (tf[q] * (k1 + 1)) / (tf[q] + k1 * (1 - b + b * dl / avgdl))
        scores[did] = s
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return [d for d, s in ranked[:top]], ranked[0][0] if ranked else None

docs = parse_kb(KB)
with open(TS, 'r', encoding='utf-8') as f:
    dataset = json.load(f)

print(f"知识库文档数: {len(docs)} | 测试题数: {len(dataset)}\n")
print(f"{'题目':<6} {'type':<9} {'ground_truth':<20} {'BM25 Top1':<10} {'Top1命中':<8} {'Top5':<30} {'R@5'}")
r1 = r5 = neg_ok = 0
for item in dataset:
    qid, q, qtype, gt = item['id'], item['question'], item['type'], item.get('ground_truth', [])
    top5, top1 = bm25(docs, q)
    hit1 = top1 in gt if gt else None
    hit5 = len(set(top5) & set(gt)) / len(gt) if gt else 0.0
    if gt:
        r1 += 1 if hit1 else 0
        r5 += 1 if hit5 >= 0.7 else 0
    else:
        neg_ok += 1 if not top5 else 0
    print(f"{qid:<6} {qtype:<9} {str(gt):<20} {str(top1):<10} {str(hit1):<8} {str(top5):<30} {hit5:.2f}")

print(f"\n=== 汇总 ===")
print(f"非 negative 题 ({len(dataset)-sum(1 for t in dataset if t['type']=='negative')} 题): Top1 命中 {r1} 题, Recall@5≥0.7 共 {r5} 题")
print(f"negative 题 ({sum(1 for t in dataset if t['type']=='negative')} 题): 空检索 {neg_ok} 题")
