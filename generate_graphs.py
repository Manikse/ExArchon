import matplotlib.pyplot as plt
import matplotlib.patches as patches

def draw_box(ax, x, y, w, h, text, color="#E8F4F8", edge="#1D3557"):
    """Малює блок схеми з округленими кутами."""
    # ВИПРАВЛЕНО: Використовуємо FancyBboxPatch замість Rectangle
    box = patches.FancyBboxPatch((x, y), w, h, 
                                 boxstyle="round,pad=0.1", 
                                 facecolor=color, 
                                 edgecolor=edge, 
                                 linewidth=2)
    ax.add_patch(box)
    ax.text(x + w/2, y + h/2, text, ha='center', va='center', fontsize=9, fontweight='bold', color=edge)

def draw_arrow(ax, x1, y1, x2, y2):
    """Малює стрілку потоку."""
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="->", color="#1D3557", lw=1.5))

def create_ingestion_diagram():
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 6)
    ax.axis('off')

    # 1. Generators (Left)
    draw_box(ax, 0.5, 4, 2, 0.8, "IMU\n(>1000 Hz)")
    draw_box(ax, 0.5, 2.5, 2, 0.8, "LiDAR")
    draw_box(ax, 0.5, 1, 2, 0.8, "Encoders")

    # 2. DMA & Zero-Copy (Buses)
    draw_arrow(ax, 2.5, 4.4, 3.5, 3) # IMU -> DMA
    draw_arrow(ax, 2.5, 2.9, 3.5, 3) # LiDAR -> DMA
    draw_arrow(ax, 2.5, 1.4, 3.5, 3) # Encoders -> DMA
    draw_box(ax, 3.5, 2.6, 1.5, 0.8, "DMA\nZero-Copy", color="#A8DADC")

    # 3. Middle: Ring Buffer
    draw_arrow(ax, 5.0, 3, 6, 3)
    draw_box(ax, 6, 2.2, 2.5, 1.6, "Shadow Context\nRing Buffer\n(Lock-Free, Aligned)", color="#F1FAEE")

    # 4. Filters (Analysis)
    draw_arrow(ax, 8.5, 3, 9.5, 3)
    draw_box(ax, 9.5, 2.2, 2, 0.8, "EKF\n(Kalman)", color="#A8DADC")
    draw_box(ax, 9.5, 3.2, 2, 0.8, r"$\Delta$-Encoding", color="#A8DADC")

    # 5. Right: Cognitive Gateway
    draw_arrow(ax, 11.5, 3, 12.5, 3)
    # Звужуючий канал
    polygon = patches.Polygon([[12.5, 3.8], [12.5, 2.2], [13.5, 2.8], [13.5, 3.2]], 
                              closed=True, facecolor="#E63946", edgecolor="#1D3557")
    ax.add_patch(polygon)
    ax.text(14, 3, "Cognitive\nSnapshot\n(to LLM)", ha='left', va='center', fontweight='bold', color="#E63946")

    # Titles and Annotations
    plt.title("Fig 4: Sensory Ingestion Pipeline Topology", fontsize=14, fontweight='bold', pad=20)
    
    # Легенда зон
    ax.text(1, 5.5, "Physical Domain", fontsize=10, style='italic', color="#457B9D")
    ax.text(6, 5.5, "System Kernel (OS)", fontsize=10, style='italic', color="#457B9D")
    ax.text(10, 5.5, "Cognitive Gateway", fontsize=10, style='italic', color="#E63946")
    
    plt.tight_layout()
    plt.savefig('graph_4_ingestion_pipeline.pdf', format='pdf', dpi=300)
    print("✅ Графік 4 (Топологія Pipeline) успішно збережено як graph_4_ingestion_pipeline.pdf")

if __name__ == "__main__":
    create_ingestion_diagram()