import pandas as pd
import numpy as np

# 读取数据
df = pd.read_csv('core_general_kmeans_result.csv', low_memory=False)

# 分析各群组特征
print("=" * 60)
print("各群組統計分析")
print("=" * 60)

clusters = sorted(df['cluster'].unique())

for cluster in clusters:
    cluster_data = df[df['cluster'] == cluster]
    print(f"\n【Cluster {cluster}】")
    print(f"  課程數量: {len(cluster_data)}")
    print(f"  中籤率 - 平均: {cluster_data['中籤率'].mean():.3f}, 範圍: {cluster_data['中籤率'].min():.3f} ~ {cluster_data['中籤率'].max():.3f}")
    print(f"  飽和度 - 平均: {cluster_data['飽和度'].mean():.3f}, 範圍: {cluster_data['飽和度'].min():.3f} ~ {cluster_data['飽和度'].max():.3f}")
    
    # 分析特征
    avg_selection_rate = cluster_data['中籤率'].mean()
    avg_saturation = cluster_data['飽和度'].mean()
    
    print(f"  特徵描述:")
    if avg_selection_rate < 0.3 and avg_saturation > 1.0:
        print(f"    → 競爭激烈、超額選課（登記人數多但中籤率低，選上人數超過上限）")
    elif avg_selection_rate > 0.7 and avg_saturation > 1.0:
        print(f"    → 熱門課程、超額選課（中籤率高但選上人數超過上限）")
    elif avg_selection_rate < 0.3 and avg_saturation < 0.8:
        print(f"    → 競爭激烈、未滿額（登記人數多但中籤率低，且未滿額）")
    elif avg_selection_rate > 0.7 and avg_saturation < 0.8:
        print(f"    → 容易選上、未滿額（中籤率高且未滿額）")
    else:
        print(f"    → 中等競爭程度")

print("\n" + "=" * 60)
