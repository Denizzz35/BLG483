import threading
import urllib.request
from urllib.parse import urljoin, parse_qs
from html.parser import HTMLParser
import re
from collections import Counter
import json
import os
import time
import string
from http.server import HTTPServer, BaseHTTPRequestHandler
import random

STORAGE_DIR = "data/storage"
JOBS_DIR = "data/jobs"
VISITED_FILE = "data/visited_urls.data"

for d in [STORAGE_DIR, JOBS_DIR]:
    if not os.path.exists(d):
        os.makedirs(d)
if not os.path.exists(VISITED_FILE):
    open(VISITED_FILE, 'w').close()

file_locks = {char: threading.Lock() for char in string.ascii_lowercase}
file_locks['visited'] = threading.Lock()

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
                    full_url = urljoin(self.base_url, value).split('#')[0]
                    if full_url.startswith('http'):
                        self.links.add(full_url)

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.text_content.append(text)

    def get_word_frequencies(self):
        text = " ".join(self.text_content)
        words = re.findall(r'[a-z]+', text.lower())
        return Counter(words)

class CrawlerThread(threading.Thread):
    def __init__(self, origin, max_depth, hit_rate, queue_capacity, max_urls):
        super().__init__()
        self.origin = origin
        self.max_depth = max_depth
        self.hit_rate = hit_rate
        self.queue_capacity = queue_capacity
        self.max_urls = max_urls
        
        self.crawler_id = None
        self.started_event = threading.Event()

    def run(self):
        self.crawler_id = f"{int(time.time())}_{self.ident}"
        self.started_event.set()

        job_file = os.path.join(JOBS_DIR, f"{self.crawler_id}.data")
        
        def log_status(status, logs, queue):
            with open(job_file, 'w') as f:
                json.dump({"status": status, "logs": logs, "queue": queue}, f)

        logs = [f"Started job {self.crawler_id} for {self.origin}"]
        queue = [(self.origin, self.origin, 0)]
        log_status("running", logs, queue)

        last_request_time = 0
        urls_visited_count = 0

        while queue:
            if self.max_urls > 0 and urls_visited_count >= self.max_urls:
                logs.append(f"Reached Max URLs limit ({self.max_urls}). Stopping crawl.")
                break
            current_url, origin_url, depth = queue.pop(0)
            log_status("running", logs, queue)

            if self.hit_rate > 0:
                base_wait = 1.0 / self.hit_rate
                time_to_wait = random.uniform(base_wait * 0.7, base_wait * 1.5)
                elapsed = time.time() - last_request_time
                if elapsed < time_to_wait:
                    time.sleep(time_to_wait - elapsed)
            last_request_time = time.time()

            with file_locks['visited']:
                with open(VISITED_FILE, 'r') as f:
                    visited = set(f.read().splitlines())
                
                if current_url in visited:
                    continue
                    
                with open(VISITED_FILE, 'a') as f:
                    f.write(current_url + "\n")
                visited.add(current_url)

            try:
                req = urllib.request.Request(current_url, headers={'User-Agent': 'BrightwaveCrawler/1.0'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    if response.status != 200 or 'text/html' not in response.info().get_content_type():
                        continue
                    html = response.read().decode('utf-8', errors='ignore')
            except Exception as e:
                logs.append(f"Error fetching {current_url}: {str(e)}")
                continue

            parser = PageParser(current_url)
            parser.feed(html)
            word_freqs = parser.get_word_frequencies()

            for word, count in word_freqs.items():
                first_letter = word[0]
                if first_letter not in file_locks:
                    continue
                    
                lock = file_locks[first_letter]
                letter_file = os.path.join(STORAGE_DIR, f"{first_letter}.data")
                
                with lock:
                    if os.path.exists(letter_file):
                        try:
                            with open(letter_file, 'r') as f:
                                data = json.load(f)
                        except json.JSONDecodeError:
                            data = {}
                    else:
                        data = {}

                    if word not in data:
                        data[word] = []
                    data[word].append({
                        "url": current_url,
                        "origin": origin_url,
                        "depth": depth,
                        "frequency": count
                    })

                    with open(letter_file, 'w') as f:
                        json.dump(data, f)

            logs.append(f"Indexed {current_url} (Depth {depth})")

            if depth < self.max_depth:
                for link in parser.links:
                    if link not in visited:
                        if self.queue_capacity > 0 and len(queue) >= self.queue_capacity:
                            logs.append("Queue capacity reached. Pausing link ingestion for this page.")
                            break 
                        queue.append((link, origin_url, depth + 1))

        logs.append(f"Job {self.crawler_id} completed.")
        log_status("completed", logs, [])

class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_html(self.get_ui_html())
        elif self.path.startswith('/api/status'):
            query = parse_qs(self.path.split('?')[1]) if '?' in self.path else {}
            crawler_id = query.get('id', [''])[0]
            job_file = os.path.join(JOBS_DIR, f"{crawler_id}.data")
            if os.path.exists(job_file):
                with open(job_file, 'r') as f:
                    self.send_json(json.load(f))
            else:
                self.send_json({"error": "Job not found"})
        elif self.path.startswith('/search'):
            query = parse_qs(self.path.split('?')[1]).get('query', [''])[0].lower()
            results = self.perform_search(query)
            self.send_json(results)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == '/api/crawl':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            params = parse_qs(post_data.decode('utf-8'))
            
            origin = params.get('origin', [''])[0]
            depth = int(params.get('depth', ['1'])[0])
            hit_rate = float(params.get('hit_rate', ['0'])[0])
            queue_capacity = int(params.get('queue_capacity', ['0'])[0])
            max_urls = int(params.get('max_urls', ['0'])[0])            
            worker = CrawlerThread(origin, depth, hit_rate, queue_capacity, max_urls)
            worker.start()
            worker.started_event.wait() 
            
            self.send_json({"message": "Job started", "crawler_id": worker.crawler_id})

        elif self.path == '/api/clear':
            with file_locks['visited']:
                for f in os.listdir(STORAGE_DIR):
                    if f.endswith('.data'):
                        os.remove(os.path.join(STORAGE_DIR, f))

                for f in os.listdir(JOBS_DIR):
                    if f.endswith('.data'):
                        os.remove(os.path.join(JOBS_DIR, f))

                open(VISITED_FILE, 'w').close()
            self.send_json({"message": "All database files cleared successfully!"})
            
    def perform_search(self, query): 
        words = re.findall(r'[a-z]+', query.lower()) 
        if not words: return [] 
         
        all_results = [] 
        for word in words: 
            letter = word[0] 
            letter_file = os.path.join(STORAGE_DIR, f"{letter}.data") 
            if os.path.exists(letter_file): 
                with open(letter_file, 'r') as f: 
                    data = json.load(f) 
                    
                    if word in data: 
                        for entry in data[word]: 
                            freq = entry['frequency'] 
                            depth = entry['depth'] 

                            score = (freq * 10) + 1000 - (depth * 5) 

                            all_results.append({ 
                                "url": entry['url'], 
                                "origin": entry['origin'], 
                                "depth": depth, 
                                "frequency": freq, 
                                "relevance_score": score 
                            }) 
                         
        all_results.sort(key=lambda x: x['relevance_score'], reverse=True) 
        return all_results

    def send_html(self, content):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(content.encode('utf-8'))

    def send_json(self, data):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def get_ui_html(self):
        with open('index.html', 'r', encoding='utf-8') as f:
            return f.read()

if __name__ == '__main__':
    server = HTTPServer(('localhost', 3600), RequestHandler)
    print("Serving Web App and API on http://localhost:3600")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        server.server_close()