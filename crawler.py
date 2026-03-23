import threading
import urllib.request
import urllib.error
from urllib.parse import urljoin
from html.parser import HTMLParser
import re
from collections import Counter
import json
import os
import time
import string
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

# --- File System Setup & Locks ---
STORAGE_DIR = "storage"
JOBS_DIR = "jobs"
VISITED_FILE = "visited_urls.data"

for d in [STORAGE_DIR, JOBS_DIR]:
    if not os.path.exists(d):
        os.makedirs(d)
if not os.path.exists(VISITED_FILE):
    open(VISITED_FILE, 'w').close()

# We need locks because multiple threads might try to write to 'a.data' at the same time
file_locks = {char: threading.Lock() for char in string.ascii_lowercase}
file_locks['visited'] = threading.Lock()

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

# --- Core Crawler Job ---
def crawler_job(origin, max_depth, crawler_id):
    job_file = os.path.join(JOBS_DIR, f"{crawler_id}.data")
    
    def log_status(status, logs, queue):
        with open(job_file, 'w') as f:
            json.dump({"status": status, "logs": logs, "queue": queue}, f)

    logs = [f"Started job {crawler_id} for {origin}"]
    queue = [(origin, origin, 0)]
    log_status("running", logs, queue)

    while queue:
        current_url, origin_url, depth = queue.pop(0)
        log_status("running", logs, queue)

        # Check and update visited URLs
        with file_locks['visited']:
            with open(VISITED_FILE, 'r') as f:
                visited = set(f.read().splitlines())
            
            if current_url in visited:
                continue
                
            with open(VISITED_FILE, 'a') as f:
                f.write(current_url + "\n")
            visited.add(current_url)

        # Fetch page
        try:
            req = urllib.request.Request(current_url, headers={'User-Agent': 'LocalWebCrawler/1.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status != 200 or 'text/html' not in response.info().get_content_type():
                    continue
                html = response.read().decode('utf-8', errors='ignore')
        except Exception as e:
            logs.append(f"Error fetching {current_url}: {str(e)}")
            continue

        # Parse content
        parser = PageParser(current_url)
        parser.feed(html)
        word_freqs = parser.get_word_frequencies()

        # Save words to [letter].data
        for word, count in word_freqs.items():
            first_letter = word[0]
            if first_letter not in file_locks:
                continue
                
            lock = file_locks[first_letter]
            letter_file = os.path.join(STORAGE_DIR, f"{first_letter}.data")
            
            with lock:
                # Load existing data
                if os.path.exists(letter_file):
                    try:
                        with open(letter_file, 'r') as f:
                            data = json.load(f)
                    except json.JSONDecodeError:
                        data = {}
                else:
                    data = {}

                # Update data
                if word not in data:
                    data[word] = []
                data[word].append({
                    "url": current_url,
                    "origin": origin_url,
                    "depth": depth,
                    "frequency": count
                })

                # Write back to disk
                with open(letter_file, 'w') as f:
                    json.dump(data, f)

        logs.append(f"Indexed {current_url} (Depth {depth})")

        # Queue new links
        if depth < max_depth:
            for link in parser.links:
                if link not in visited:
                    queue.append((link, origin_url, depth + 1))

    logs.append(f"Job {crawler_id} completed.")
    log_status("completed", logs, [])

# --- Web Server & API ---
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
        elif self.path.startswith('/api/search'):
            query = parse_qs(self.path.split('?')[1]).get('q', [''])[0].lower()
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
            
            # Create Thread ID [EpochTimeCreated_ThreadID]
            epoch = int(time.time())
            # Start a dummy thread just to get its future ID, or start it and read it inside
            t = threading.Thread(target=crawler_job, args=(origin, depth, "placeholder"))
            t.start()
            crawler_id = f"{epoch}_{t.ident}"
            
            # We have to patch the crawler_id because thread.ident is only assigned AFTER start()
            # To fix this cleanly, we pass the real ID via a wrapper function.
            def thread_wrapper():
                ident = threading.current_thread().ident
                real_id = f"{epoch}_{ident}"
                crawler_job(origin, depth, real_id)
                
            # Actually, standard threading allows grabbing ident if we create a custom Thread class, 
            # but for simplicity, let's just use epoch_random or epoch_systemthreadid
            crawler_id = f"{epoch}_{threading.get_native_id()}"
            t = threading.Thread(target=crawler_job, args=(origin, depth, crawler_id))
            t.start()
            
            self.send_json({"message": "Job started", "crawler_id": crawler_id})

    def perform_search(self, query):
        words = re.findall(r'[a-z]+', query)
        if not words: return []
        
        all_results = []
        for word in words:
            letter = word[0]
            letter_file = os.path.join(STORAGE_DIR, f"{letter}.data")
            if os.path.exists(letter_file):
                with open(letter_file, 'r') as f:
                    data = json.load(f)
                    if word in data:
                        all_results.extend(data[word])
        
        # Sort by highest frequency
        all_results.sort(key=lambda x: x['frequency'], reverse=True)
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
        return """
        <!DOCTYPE html>
        <html>
        <head><title>Brightwave Crawler</title></head>
        <body style="font-family: sans-serif; padding: 20px;">
            <h1>Web Crawler Dashboard</h1>
            
            <div style="border: 1px solid #ccc; padding: 15px; margin-bottom: 20px;">
                <h3>1. Start New Crawler</h3>
                <form onsubmit="startCrawl(event)">
                    URL: <input type="text" id="origin" placeholder="https://example.com" required>
                    Depth: <input type="number" id="depth" value="1" min="0" required>
                    <button type="submit">Start Crawl</button>
                </form>
                <p id="crawl-msg"></p>
            </div>

            <div style="border: 1px solid #ccc; padding: 15px; margin-bottom: 20px;">
                <h3>2. Crawler Status (Long Polling Simulation)</h3>
                <input type="text" id="crawlerId" placeholder="Crawler ID">
                <button onclick="pollStatus()">Check Status</button>
                <pre id="status-log" style="background: #f4f4f4; padding: 10px;"></pre>
            </div>

            <div style="border: 1px solid #ccc; padding: 15px;">
                <h3>3. Search Filesystem</h3>
                <form onsubmit="searchFiles(event)">
                    Query: <input type="text" id="query" required>
                    <button type="submit">Search</button>
                </form>
                <ul id="search-results"></ul>
            </div>

            <script>
                async function startCrawl(e) {
                    e.preventDefault();
                    const origin = document.getElementById('origin').value;
                    const depth = document.getElementById('depth').value;
                    const res = await fetch('/api/crawl', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                        body: `origin=${encodeURIComponent(origin)}&depth=${depth}`
                    });
                    const data = await res.json();
                    document.getElementById('crawl-msg').innerText = "Started! ID: " + data.crawler_id;
                    document.getElementById('crawlerId').value = data.crawler_id;
                }

                async function pollStatus() {
                    const id = document.getElementById('crawlerId').value;
                    if(!id) return;
                    const res = await fetch('/api/status?id=' + id);
                    const data = await res.json();
                    if(data.error) {
                        document.getElementById('status-log').innerText = data.error;
                    } else {
                        document.getElementById('status-log').innerText = 
                            "Status: " + data.status + "\\n\\nLogs:\\n" + data.logs.join('\\n');
                    }
                }

                async function searchFiles(e) {
                    e.preventDefault();
                    const q = document.getElementById('query').value;
                    const res = await fetch('/api/search?q=' + encodeURIComponent(q));
                    const data = await res.json();
                    const ul = document.getElementById('search-results');
                    ul.innerHTML = '';
                    if(data.length === 0) ul.innerHTML = '<li>No results found on disk.</li>';
                    data.forEach(item => {
                        ul.innerHTML += `<li><b>${item.url}</b> (Origin: ${item.origin}, Depth: ${item.depth}, Freq: ${item.frequency})</li>`;
                    });
                }
            </script>
        </body>
        </html>
        """

if __name__ == '__main__':
    server = HTTPServer(('localhost', 8000), RequestHandler)
    print("Serving Web App and API on http://localhost:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        server.server_close()