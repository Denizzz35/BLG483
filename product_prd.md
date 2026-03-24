# Product Requirements Document (PRD): Multi-Threaded Web Crawler

## 1. Product Overview
The goal is to build a lightweight, self-contained web crawler and search engine. The system will ingest a seed URL, crawl web pages up to a specified depth, calculate term frequencies, and store the results in an inverted index. It must include a web-based UI for managing crawls and querying the index. 

## 2. Technical Constraints
**CRITICAL STRICT RULE:** The entire backend must be built using **only Python standard libraries**. No external dependencies (e.g., `requests`, `BeautifulSoup`, `Flask`, `FastAPI`) are allowed. 
- Networking: `urllib.request`
- Concurrency: `threading`
- Web Server: `http.server`
- HTML Parsing: `html.parser`

## 3. Core Architecture
### 3.1. Storage System (File-Based)
Do not use a relational database. Use the local file system to store data:
- `data/storage/`: Stores the inverted index. Create one JSON file per lowercase letter of the alphabet (e.g., `a.data`, `b.data`). 
- `data/jobs/`: Stores active crawler status files.
- `data/visited_urls.data`: A flat text file tracking deduplication.
- **Thread Safety:** Implement a dictionary of `threading.Lock()` objects (one for each letter file and one for the visited file) to prevent data corruption during concurrent writes.

### 3.2. Crawler Engine
- Implement a `CrawlerThread` class extending `threading.Thread`.
- **Queue:** Maintain an in-memory queue of URLs to visit: `(current_url, origin_url, depth)`.
- **Parsing:** Build a custom `PageParser` extending `HTMLParser` to extract `<a href>` tags and plaintext data.
- **Indexing:** Tokenize page text into lowercase alphabetic words. Calculate term frequencies using `collections.Counter`. Append the result to the correct `[letter].data` file under the word key, storing: `{url, origin, depth, frequency}`.
- **Rate Limiting:** Implement a randomized sleep delay based on a user-provided `hit_rate` parameter to avoid overloading target servers.

### 3.3. Search & Relevance Algorithm
- When a user searches a word, look it up in the corresponding letter `.data` file.
- Calculate the relevance score using the exact formula: `(frequency * 10) + 1000 - (depth * 5)`.
- Sort results descending by score.

## 4. API Endpoints
Implement a custom `BaseHTTPRequestHandler` to serve the UI and APIs on port `3600`:
- `GET /`: Serve `index.html`.
- `POST /api/crawl`: Accept origin, depth, hit_rate, queue_capacity, max_urls. Start thread.
- `GET /api/status?id=...`: Return current crawler status and logs.
- `GET /search?query=...`: Return JSON array of search results.
- `POST /api/clear`: Delete all `.data` files and reset state.