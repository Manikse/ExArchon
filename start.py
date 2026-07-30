import sys
import time
import subprocess
from core.env_manager import EnvManager

from core.kernel.acl.provider import ACLController
from core.kernel.acl.ollama_provider import OllamaProvider

def main():
    print(" Ініціалізація EXARCHON Hybrid Edge (Beta v0.9.0)...")

    env = EnvManager()
    ready, status = env.check_ollama()
    
    if not ready:
        if status == "Ollama not installed":
            if env.install_ollama():
                sys.exit()
        elif status == "Model missing":
            if env.provision_llama():
                print(" Модель готова. Перезапуск діагностики...")
                main()
                return
        else:
            sys.exit()

    print("\n Усі системи готові. Підключення до Системного Нексуса...")
    time.sleep(1)
    
    try:
        # 1. Запуск ядра (Твій main.py)
        print(" Запуск системного ядра (core/main.py)...")
        # subprocess.Popen не блокує термінал, а запускає процес паралельно
        server_process = subprocess.Popen([sys.executable, "core/main.py"])
        
        # Даємо ядру 2 секунди на запуск перед тим, як підключати "руки"
        time.sleep(2)
        
        # 2. Запуск клієнта / "рук" (Розкоментуй і вкажи правильний шлях до свого клієнта)
        # print("🦾 Підключення інтерфейсу (клієнт)...")
        # client_process = subprocess.Popen([sys.executable, "clients/python-node/client.py"])
        
        # Чекаємо завершення роботи сервера, щоб скрипт не закрився миттєво
        server_process.wait() 
        
    except KeyboardInterrupt:
        print("\n>>> EXARCHON: Екстрене відключення систем...")
        server_process.terminate()
        # client_process.terminate() # Розкоментуй, коли додаш клієнт
        sys.exit(0)

if __name__ == "__main__":
    main()