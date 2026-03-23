import asyncio
import threading
import urllib.request
import urllib.error
from urllib.parse import urljoin, urlparse
from html.parser import HTMLParser
import re
from collections import defaultdict
import cmd
import json
import os
#import time

# --- Native HTML Parser ---
class PageParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__()
        self.base_url = base_url
        self.links = set()
        self.text_content = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            for attr, value in attrs:
                if attr == 'href':
                    # Resolve relative URLs and strip fragments
                    full_url = urljoin(self.base_url, value).split('#')[0]
                    if full_url.startswith('http'):
                        self.links.add(full_url)

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.text_content.append(text)

    def get_words(self):
        text = " ".join(self.text_content)
        # Simple tokenization: lowercase and extract alphabetic words
        return set(re.findall(r'[a-z]+', text.lower()))

# --- Core Crawler System ---
class SearchCrawlSystem:
    def __init__(self, max_queue_depth=5000, worker_count=20, state_file="state.json"):
        # Backpressure: Bounded queue prevents unbounded memory growth
        self.queue = asyncio.Queue(maxsize=max_queue_depth)
        self.worker_count = worker_count
        self.state_file = state_file
        
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r') as f:
                data = json.load(f)
                self.visited = set(data.get('visited', []))
                self.url_metadata = data.get('url_metadata', {})
                # Convert lists back to sets for the inverted index
                self.inverted_index = defaultdict(set, {k: set(v) for k, v in data.get('inverted_index', {}).items()})
                self.indexed_count = data.get('indexed_count', 0)
                print(f"[*] Resumed state: {self.indexed_count} indexed pages loaded.")

        else:
            self.visited = set()
            self.url_metadata = {} 
            self.inverted_index = defaultdict(set) 
            self.indexed_count = 0        

        # State & Index
        self.visited = set()
        self.url_metadata = {} # URL -> (origin_url, depth)
        self.inverted_index = defaultdict(set) # word -> set(URLs)
        
        # Metrics for UI
        self.indexed_count = 0
        self.active_workers = 0
        self.is_running = False

    async def _fetch(self, url):
        """Runs the blocking urllib call in a thread pool to unblock the async loop."""
        loop = asyncio.get_running_loop()
        req = urllib.request.Request(url, headers={'User-Agent': 'NativeCrawler/1.0'})
        
        def do_request():
            try:
                with urllib.request.urlopen(req, timeout=3) as response:
                    if 'text/html' in response.info().get_content_type():
                        return response.read().decode('utf-8', errors='ignore')
            except Exception:
                pass # Suppress HTTP/SSL errors for the sake of the exercise
            return ""
            
        return await loop.run_in_executor(None, do_request)

    async def _worker(self):
        """Worker task that consumes URLs from the queue."""
        while True:
            current_url, origin, depth, max_depth = await self.queue.get()
            self.active_workers += 1
            
            try:
                html = await self._fetch(current_url)
                if html:
                    parser = PageParser(current_url)
                    parser.feed(html)
                    
                    # 1. Update Inverted Index (Synchronous & safe within async loop)
                    words = parser.get_words()
                    for word in words:
                        self.inverted_index[word].add(current_url)
                    
                    self.url_metadata[current_url] = (origin, depth)
                    self.indexed_count += 1

                    # 2. Queue newly discovered links if we haven't hit max depth
                    if depth < max_depth:
                        for link in parser.links:
                            if link not in self.visited:
                                self.visited.add(link)
                                # This will block if queue is full, applying backpressure
                                await self.queue.put((link, origin, depth + 1, max_depth))
            finally:
                self.active_workers -= 1
                self.queue.task_done()

    async def index(self, origin, k):
        """Initiates the indexing process for a given origin and depth k."""
        if origin not in self.visited:
            self.visited.add(origin)
            await self.queue.put((origin, origin, 0, k))
            
            # Start workers if they aren't running yet
            if not self.is_running:
                self.is_running = True
                self.workers = [asyncio.create_task(self._worker()) for _ in range(self.worker_count)]

    async def search(self, query):
        """Searches the inverted index for the query."""
        words = set(re.findall(r'[a-z]+', query.lower()))
        if not words:
            return []

        # Intersect URL sets for all words in the query
        match_sets = [self.inverted_index.get(word, set()) for word in words]
        relevant_urls = set.intersection(*match_sets) if match_sets else set()

        results = []
        for url in relevant_urls:
            origin, depth = self.url_metadata[url]
            results.append((url, origin, depth))
        return results

    def get_status(self):
        return {
            "queue_depth": self.queue.qsize(),
            "active_workers": self.active_workers,
            "total_indexed": self.indexed_count,
            "total_discovered": len(self.visited),
            "backpressure_status": "HIGH" if self.queue.full() else ("MODERATE" if self.queue.qsize() > self.queue.maxsize * 0.8 else "NORMAL")
        }

    def save_state(self):
        """Serializes the current state to disk."""
        print("\n[*] Saving system state to disk...")
        data = {
            'visited': list(self.visited),
            'url_metadata': self.url_metadata,
            # Sets are not JSON serializable, convert to lists
            'inverted_index': {k: list(v) for k, v in self.inverted_index.items()},
            'indexed_count': self.indexed_count
        }
        with open(self.state_file, 'w') as f:
            json.dump(data, f)
        print("[*] State saved successfully.")

# --- CLI / UI ---
class CrawlerCLI(cmd.Cmd):
    intro = "\n=== Web Crawler CLI ===\nType 'help' or '?' to list commands."
    prompt = "crawler> "

    def __init__(self, system, loop):
        super().__init__()
        self.system = system
        self.loop = loop

    def do_index(self, arg):
        """index <origin_url> <k>\nInitiate crawling from origin_url to depth k."""
        args = arg.split()
        if len(args) != 2:
            print("Usage: index <url> <depth>")
            return
        origin, k = args[0], int(args[1])
        # Submit the coroutine to the background event loop safely
        asyncio.run_coroutine_threadsafe(self.system.index(origin, k), self.loop)
        print(f"[*] Queued indexing for {origin} at depth {k}.")

    def do_search(self, arg):
        """search <query>\nSearch the indexed pages for a query string."""
        if not arg:
            print("Usage: search <query>")
            return
        
        # Schedule the search and wait for the result
        future = asyncio.run_coroutine_threadsafe(self.system.search(arg), self.loop)
        results = future.result(timeout=5)
        
        if not results:
            print("[-] No results found.")
        else:
            print(f"\n[+] Found {len(results)} results:")
            for url, origin, depth in results:
                print(f"    - URL: {url} | Origin: {origin} | Depth: {depth}")
            print()

    def do_status(self, arg):
        """status\nView current system state and backpressure metrics."""
        stats = self.system.get_status()
        print("\n--- System Status ---")
        print(f"Indexed Pages   : {stats['total_indexed']}")
        print(f"Discovered URLs : {stats['total_discovered']}")
        print(f"Active Workers  : {stats['active_workers']}")
        print(f"Queue Depth     : {stats['queue_depth']}")
        print(f"System Load     : {stats['backpressure_status']}\n")

    def do_quit(self, arg):
        """quit\nExit the application."""
        print("Shutting down...")
        self.system.save_state() # <-- NEW: Save state before quitting
        self.loop.call_soon_threadsafe(self.loop.stop)
        return True


def run_event_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

if __name__ == '__main__':
    # Initialize the core system
    crawler_system = SearchCrawlSystem(max_queue_depth=1000, worker_count=10)
    
    # Start the asyncio event loop in a background thread
    bg_loop = asyncio.new_event_loop()
    bg_thread = threading.Thread(target=run_event_loop, args=(bg_loop,), daemon=True)
    bg_thread.start()
    
    # Run the interactive CLI on the main thread
    try:
        CrawlerCLI(crawler_system, bg_loop).cmdloop()
    except KeyboardInterrupt:
        bg_loop.call_soon_threadsafe(bg_loop.stop)