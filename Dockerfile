# Використовуємо легкий образ Python
FROM python:3.11-slim

# Встановлюємо системні залежності для драйверів
RUN apt-get update && apt-get install -y \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Створюємо робочу директорію
WORKDIR /app

# Копіюємо файли залежностей
COPY core/requirements.txt .
# Якщо requirements.txt ще немає, ми його створимо нижче

# Встановлюємо бібліотеки
RUN pip install --no-cache-dir -r requirements.txt

# Копіюємо весь код Ядра
COPY core/ .

# Створюємо папку для робочого простору драйверів та пам'яті
RUN mkdir -p /app/kernel_workspace /app/data

# Відкриваємо порт API
EXPOSE 8000

# Запускаємо сервер
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--lifespan", "on"]