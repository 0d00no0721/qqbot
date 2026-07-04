import sys
import os

# 确保能导入上级目录的 set_env.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import set_env  # 设置缓存路径到 E:\QQbot\models_cache

from transformers import pipeline


def main():
    print("正在加载零样本分类模型 cross-encoder/nli-MiniLM2-L6-H768 ...")
    classifier = pipeline(
        "zero-shot-classification",
        model="cross-encoder/nli-MiniLM2-L6-H768",
    )
    print("模型加载成功！\n")

    candidate_labels = ["日常生活", "严肃政治", "战争雷霆"]
    text = "今天中午吃什么？"
    print(f"输入文本: {text}")
    print(f"候选标签: {candidate_labels}")

    result = classifier(text, candidate_labels)
    print("\n分类结果:")
    for label, score in zip(result["labels"], result["scores"]):
        print(f"  {label}: {score:.4f}")

    best = result["labels"][0]
    best_score = result["scores"][0]
    print(f"\n最可能的标签: {best} (置信度: {best_score:.4f})")


if __name__ == "__main__":
    main()
