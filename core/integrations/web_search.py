import asyncio
from duckduckgo_search import DDGS


class WebSearchDriver:
    """
    Драйвер веб-пошуку. Дає Ядру можливість гуглити актуальну інформацію.
    """
    def __init__(self):
        self.name = "WebSearch"

    async def execute(self, query: str) -> str:
        print(f"\n[Driver: {self.name}] 🌐 Searching the web for: '{query}'...")
        
        try:
            def perform_search():
                # Використовуємо нову бібліотеку ddgs
                return DDGS().text(query, max_results=3)
            
            results = await asyncio.to_thread(perform_search)
            
            if not results:
                return f"SYSTEM REPORT: The web search returned exactly 0 results for '{query}'. Do not hallucinate. Tell the user you couldn't find the data."
            
            formatted_results = []
            for i, r in enumerate(results, 1):
                formatted_results.append(f"Result {i}:\nTitle: {r.get('title')}\nSnippet: {r.get('body')}\nLink: {r.get('href')}")
                
            final_output = "\n\n".join(formatted_results)
            return f"SUCCESS: Found the following information:\n{final_output}"
            
        except Exception as e:
            return f"SYSTEM REPORT: Web search FAILED with error: {str(e)}. Tell the user the search tool is broken."