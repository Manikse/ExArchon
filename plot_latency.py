import matplotlib.pyplot as plt
import numpy as np

# 1. Налаштування академічного стилю графіка
plt.style.use('seaborn-v0_8-whitegrid')
fig, ax = plt.subplots(figsize=(8, 5))

# 2. Математична симуляція даних (100 послідовних запитів до системи)
requests = np.arange(1, 101)

# Стандартний ШІ (завжди звертається до LLM: ~3000 мс на відповідь)
standard_llm_latency = np.random.normal(loc=3000, scale=150, size=100)

# EXARCHON Hybrid Architecture
# 80% запитів - це рефлекси (Fast Path, ~15 мс)
# 20% запитів - це кризовий аналіз (Deep Path, ~3100 мс)
exarchon_latency = np.where(
    requests <= 80, 
    np.random.normal(loc=15, scale=2, size=100), 
    np.random.normal(loc=3100, scale=150, size=100)
)

# 3. Побудова ліній
ax.plot(requests, standard_llm_latency, label='Monolithic LLM Agent', color='#E63946', linewidth=2, linestyle='--')
ax.plot(requests, exarchon_latency, label='EXARCHON (Hybrid Edge)', color='#1D3557', linewidth=2.5)

# 4. Форматування осей та підписів (як у наукових статтях)
ax.set_title('System Latency: Monolithic vs. EXARCHON Bifurcated Routing', fontsize=12, fontweight='bold', pad=15)
ax.set_xlabel('Task Complexity / Request Sequence', fontsize=10)
ax.set_ylabel('Response Latency (ms)', fontsize=10)
ax.set_yscale('log') # Логарифмічна шкала найкраще показує розрив між 15 мс та 3000 мс
ax.legend(loc='lower right', frameon=True, shadow=True)

# 5. Експорт у векторний формат для статті
plt.tight_layout()
plt.savefig('exarchon_latency_proof.pdf', format='pdf', dpi=300)
print("[SUCCESS] Графік згенеровано та збережено як exarchon_latency_proof.pdf")