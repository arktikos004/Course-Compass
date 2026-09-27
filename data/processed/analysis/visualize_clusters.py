import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

# Set style
try:
    plt.style.use('seaborn-v0_8')
except:
    try:
        plt.style.use('seaborn')
    except:
        plt.style.use('default')
sns.set_palette("husl")

# Read data
file_path = "core_general_kmeans_result.csv"
df = pd.read_csv(file_path, low_memory=False)

print(f"Total rows: {len(df)}")
print(f"Clusters: {sorted(df['cluster'].unique())}")
print(f"\nCluster distribution:")
print(df['cluster'].value_counts().sort_index())

# English column mapping
col_mapping = {
    '中籤率': 'Selection_Rate',
    '飽和度': 'Saturation',
    'cluster': 'Cluster',
    '課程名稱': 'Course_Name',
    '學分': 'Credits',
    '登記人數': 'Registered',
    '選上人數': 'Selected',
    '上限人數': 'Capacity'
}

# Create output directory
output_dir = Path("cluster_visualizations")
output_dir.mkdir(exist_ok=True)

# ============================================
# Figure 1: Scatter Plot - Selection Rate vs Saturation by Cluster
# ============================================
fig1, ax1 = plt.subplots(figsize=(12, 8))

clusters = sorted(df['cluster'].unique())
colors = plt.cm.tab10(np.linspace(0, 1, len(clusters)))

for i, cluster in enumerate(clusters):
    cluster_data = df[df['cluster'] == cluster]
    ax1.scatter(
        cluster_data['飽和度'],
        cluster_data['中籤率'],
        label=f'Cluster {cluster} (n={len(cluster_data)})',
        color=colors[i],
        alpha=0.6,
        s=50,
        edgecolors='black',
        linewidth=0.5
    )

ax1.set_xlabel('Saturation (Selected / Capacity)', fontsize=12, fontweight='bold')
ax1.set_ylabel('Selection Rate (Selected / Registered)', fontsize=12, fontweight='bold')
ax1.set_title('K-Means Clustering: Selection Rate vs Saturation', fontsize=14, fontweight='bold')
ax1.legend(title='Cluster', fontsize=10, title_fontsize=11)
ax1.grid(True, alpha=0.3, linestyle='--')
ax1.set_facecolor('#f8f9fa')

plt.tight_layout()
plt.savefig(output_dir / 'scatter_selection_vs_saturation.png', dpi=300, bbox_inches='tight')
print(f"\nSaved: {output_dir / 'scatter_selection_vs_saturation.png'}")
plt.close()

# ============================================
# Figure 2: Cluster Distribution (Bar Chart)
# ============================================
fig2, ax2 = plt.subplots(figsize=(10, 6))

cluster_counts = df['cluster'].value_counts().sort_index()
bars = ax2.bar(
    [f'Cluster {c}' for c in cluster_counts.index],
    cluster_counts.values,
    color=colors[:len(cluster_counts)],
    edgecolor='black',
    linewidth=1.5,
    alpha=0.8
)

# Add value labels on bars
for bar in bars:
    height = bar.get_height()
    ax2.text(bar.get_x() + bar.get_width()/2., height,
             f'{int(height)}',
             ha='center', va='bottom', fontsize=11, fontweight='bold')

ax2.set_xlabel('Cluster', fontsize=12, fontweight='bold')
ax2.set_ylabel('Number of Courses', fontsize=12, fontweight='bold')
ax2.set_title('Distribution of Courses Across Clusters', fontsize=14, fontweight='bold')
ax2.grid(True, alpha=0.3, axis='y', linestyle='--')
ax2.set_facecolor('#f8f9fa')

plt.tight_layout()
plt.savefig(output_dir / 'cluster_distribution.png', dpi=300, bbox_inches='tight')
print(f"Saved: {output_dir / 'cluster_distribution.png'}")
plt.close()

# ============================================
# Figure 3: Cluster Statistics (Box Plot)
# ============================================
fig3, axes = plt.subplots(1, 2, figsize=(14, 6))

# Box plot for Selection Rate
bp1 = axes[0].boxplot(
    [df[df['cluster'] == c]['中籤率'].values for c in clusters],
    labels=[f'Cluster {c}' for c in clusters],
    patch_artist=True,
    showmeans=True,
    meanline=True
)

for patch, color in zip(bp1['boxes'], colors[:len(clusters)]):
    patch.set_facecolor(color)
    patch.set_alpha(0.7)

axes[0].set_ylabel('Selection Rate', fontsize=12, fontweight='bold')
axes[0].set_title('Selection Rate Distribution by Cluster', fontsize=13, fontweight='bold')
axes[0].grid(True, alpha=0.3, axis='y', linestyle='--')
axes[0].set_facecolor('#f8f9fa')

# Box plot for Saturation
bp2 = axes[1].boxplot(
    [df[df['cluster'] == c]['飽和度'].values for c in clusters],
    labels=[f'Cluster {c}' for c in clusters],
    patch_artist=True,
    showmeans=True,
    meanline=True
)

for patch, color in zip(bp2['boxes'], colors[:len(clusters)]):
    patch.set_facecolor(color)
    patch.set_alpha(0.7)

axes[1].set_ylabel('Saturation', fontsize=12, fontweight='bold')
axes[1].set_title('Saturation Distribution by Cluster', fontsize=13, fontweight='bold')
axes[1].grid(True, alpha=0.3, axis='y', linestyle='--')
axes[1].set_facecolor('#f8f9fa')

plt.tight_layout()
plt.savefig(output_dir / 'cluster_statistics_boxplot.png', dpi=300, bbox_inches='tight')
print(f"Saved: {output_dir / 'cluster_statistics_boxplot.png'}")
plt.close()

# ============================================
# Figure 4: Cluster Statistics Summary Table (Visual)
# ============================================
cluster_stats = df.groupby('cluster').agg({
    '中籤率': ['mean', 'std', 'min', 'max'],
    '飽和度': ['mean', 'std', 'min', 'max'],
    'cluster': 'count'
}).round(3)

cluster_stats.columns = ['Selection_Rate_Mean', 'Selection_Rate_Std', 'Selection_Rate_Min', 'Selection_Rate_Max',
                         'Saturation_Mean', 'Saturation_Std', 'Saturation_Min', 'Saturation_Max', 'Count']

fig4, ax4 = plt.subplots(figsize=(14, 8))
ax4.axis('tight')
ax4.axis('off')

table_data = []
for cluster in sorted(cluster_stats.index):
    row = [
        f'Cluster {cluster}',
        f"{cluster_stats.loc[cluster, 'Count']:.0f}",
        f"{cluster_stats.loc[cluster, 'Selection_Rate_Mean']:.3f}",
        f"{cluster_stats.loc[cluster, 'Selection_Rate_Std']:.3f}",
        f"{cluster_stats.loc[cluster, 'Saturation_Mean']:.3f}",
        f"{cluster_stats.loc[cluster, 'Saturation_Std']:.3f}"
    ]
    table_data.append(row)

table = ax4.table(
    cellText=table_data,
    colLabels=['Cluster', 'Count', 'Selection Rate\n(Mean)', 'Selection Rate\n(Std)', 
               'Saturation\n(Mean)', 'Saturation\n(Std)'],
    cellLoc='center',
    loc='center',
    bbox=[0, 0, 1, 1]
)

table.auto_set_font_size(False)
table.set_fontsize(10)
table.scale(1, 2)

# Style header
for i in range(6):
    table[(0, i)].set_facecolor('#4a90e2')
    table[(0, i)].set_text_props(weight='bold', color='white')

# Alternate row colors
for i in range(1, len(table_data) + 1):
    for j in range(6):
        if i % 2 == 0:
            table[(i, j)].set_facecolor('#f0f0f0')
        else:
            table[(i, j)].set_facecolor('white')

ax4.set_title('Cluster Statistics Summary', fontsize=16, fontweight='bold', pad=20)

plt.savefig(output_dir / 'cluster_statistics_table.png', dpi=300, bbox_inches='tight')
print(f"Saved: {output_dir / 'cluster_statistics_table.png'}")
plt.close()

# ============================================
# Figure 5: Violin Plot for Distribution
# ============================================
fig5, axes = plt.subplots(1, 2, figsize=(14, 6))

# Violin plot for Selection Rate
data_list_sr = [df[df['cluster'] == c]['中籤率'].values for c in clusters]
parts1 = axes[0].violinplot(data_list_sr, positions=range(len(clusters)), showmeans=True, showmedians=True)

for pc, color in zip(parts1['bodies'], colors[:len(clusters)]):
    pc.set_facecolor(color)
    pc.set_alpha(0.7)

axes[0].set_xticks(range(len(clusters)))
axes[0].set_xticklabels([f'Cluster {c}' for c in clusters])
axes[0].set_ylabel('Selection Rate', fontsize=12, fontweight='bold')
axes[0].set_title('Selection Rate Distribution by Cluster', fontsize=13, fontweight='bold')
axes[0].grid(True, alpha=0.3, axis='y', linestyle='--')
axes[0].set_facecolor('#f8f9fa')

# Violin plot for Saturation
data_list_sat = [df[df['cluster'] == c]['飽和度'].values for c in clusters]
parts2 = axes[1].violinplot(data_list_sat, positions=range(len(clusters)), showmeans=True, showmedians=True)

for pc, color in zip(parts2['bodies'], colors[:len(clusters)]):
    pc.set_facecolor(color)
    pc.set_alpha(0.7)

axes[1].set_xticks(range(len(clusters)))
axes[1].set_xticklabels([f'Cluster {c}' for c in clusters])
axes[1].set_ylabel('Saturation', fontsize=12, fontweight='bold')
axes[1].set_title('Saturation Distribution by Cluster', fontsize=13, fontweight='bold')
axes[1].grid(True, alpha=0.3, axis='y', linestyle='--')
axes[1].set_facecolor('#f8f9fa')

plt.tight_layout()
plt.savefig(output_dir / 'cluster_distribution_violin.png', dpi=300, bbox_inches='tight')
print(f"Saved: {output_dir / 'cluster_distribution_violin.png'}")
plt.close()

# ============================================
# Figure 6: Heatmap of Cluster Characteristics
# ============================================
fig6, ax6 = plt.subplots(figsize=(10, 6))

heatmap_data = cluster_stats[['Selection_Rate_Mean', 'Saturation_Mean']].T
sns.heatmap(
    heatmap_data,
    annot=True,
    fmt='.3f',
    cmap='YlOrRd',
    cbar_kws={'label': 'Value'},
    linewidths=1,
    linecolor='black',
    ax=ax6
)

ax6.set_xlabel('Cluster', fontsize=12, fontweight='bold')
ax6.set_ylabel('Metric', fontsize=12, fontweight='bold')
ax6.set_title('Cluster Characteristics Heatmap', fontsize=14, fontweight='bold')
ax6.set_yticklabels(['Selection Rate\n(Mean)', 'Saturation\n(Mean)'])

plt.tight_layout()
plt.savefig(output_dir / 'cluster_heatmap.png', dpi=300, bbox_inches='tight')
print(f"Saved: {output_dir / 'cluster_heatmap.png'}")
plt.close()

print(f"\n{'='*60}")
print("All visualizations saved successfully!")
print(f"Output directory: {output_dir.absolute()}")
print(f"{'='*60}")
