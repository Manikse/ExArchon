import os

output_file = "exarchon_full_code.txt"
# Ігноруємо системні папки, віртуалки та кеш
ignore_dirs = {
    "__pycache__",
    "venv",
    ".git",
    "site-packages",
    "Scripts",
    "Lib",
    "share",
    "include",
}

with open(output_file, "w", encoding="utf-8") as outfile:
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        for file in files:
            if file.endswith(".py"):
                path = os.path.join(root, file)
                outfile.write(f"\n\n{'='*50}\n")
                outfile.write(f"FILE PATH: {path}\n")
                outfile.write(f"{'='*50}\n\n")
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        outfile.write(f.read())
                except Exception as e:
                    outfile.write(f"# Помилка читання файлу: {e}")

print(f"Готово! Весь кодовий каркас Ексархону запаковано у файл: {output_file}")