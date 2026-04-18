from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import sqlite3
import aiohttp
from bs4 import BeautifulSoup
from fastapi import FastAPI, BackgroundTasks, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Tuple

# --- Database Setup (SQLite with WAL for concurrency & persistence) ---
def init_db():
    conn = sqlite3.connect("crawler.db", check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;") # Allows concurrent reads/writes
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            url TEXT PRIMARY KEY,
            origin TEXT,
            depth INTEGER,
            content TEXT
        )
    """)
    conn.commit()
    return conn

conn = init_db()

# --- Crawler State & Backpressure Configuration ---
MAX_CONCURRENT_REQUESTS = 10
MAX_QUEUE_SIZE = 5000

class CrawlerState:
    def __init__(self):
        self.queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self.visited = set()
        self.active_workers = 0
        self.is_crawling = False
        
        # Load previously visited URLs to support resuming after interruption
        cursor = conn.cursor()
        cursor.execute("SELECT url FROM pages")
        self.visited.update(row[0] for row in cursor.fetchall())

state = CrawlerState()
app = FastAPI(title="Web Crawler & Search")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Crawler Logic ---
async def fetch_page(session: aiohttp.ClientSession, url: str) -> str:
    try:
        async with session.get(url, timeout=5) as response:
            if response.status == 200 and 'text/html' in response.headers.get('Content-Type', ''):
                return await response.text()
    except Exception:
        pass
    return ""

async def worker(session: aiohttp.ClientSession, max_depth: int):
    while state.is_crawling:
        try:
            # Backpressure: Wait for item in queue
            url, origin, depth = await asyncio.wait_for(state.queue.get(), timeout=2.0)
        except asyncio.TimeoutError:
            if state.queue.empty() and state.active_workers == 0:
                state.is_crawling = False # Queue is empty and no one is working
            continue

        state.active_workers += 1
        
        # Rate Limiting / Backpressure: Wait for semaphore
        async with state.semaphore:
            html = await fetch_page(session, url)
            
            if html:
                soup = BeautifulSoup(html, 'html.parser')
                text_content = soup.get_text(separator=' ', strip=True)
                
                # Save to DB (Persistence)
                try:
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT OR IGNORE INTO pages (url, origin, depth, content) VALUES (?, ?, ?, ?)",
                        (url, origin, depth, text_content.lower())
                    )
                    conn.commit()
                except Exception as e:
                    print(f"DB Error: {e}")

                # Extract links if we haven't hit max depth
                if depth < max_depth:
                    for link in soup.find_all('a', href=True):
                        next_url = link['href']
                        if next_url.startswith('http') and next_url not in state.visited:
                            state.visited.add(next_url)
                            try:
                                # Backpressure: Don't block forever, drop link if queue is full
                                state.queue.put_nowait((next_url, origin, depth + 1))
                            except asyncio.QueueFull:
                                pass # Queue at max capacity, dropping link to save memory

        state.active_workers -= 1
        state.queue.task_done()

async def run_crawler(origin: str, k: int):
    if state.is_crawling:
        return # Already running
    
    state.is_crawling = True
    if origin not in state.visited:
        state.visited.add(origin)
        await state.queue.put((origin, origin, 0))

    async with aiohttp.ClientSession() as session:
        workers = [asyncio.create_task(worker(session, k)) for _ in range(MAX_CONCURRENT_REQUESTS)]
        await asyncio.gather(*workers)

# --- API Endpoints ---
class IndexRequest(BaseModel):
    origin: str
    k: int

@app.post("/index")
async def start_index(req: IndexRequest, bg_tasks: BackgroundTasks):
    bg_tasks.add_task(run_crawler, req.origin, req.k)
    return {"status": "Crawler initiated", "origin": req.origin, "max_depth": req.k}

@app.get("/search")
async def search(query: str = Query(..., min_length=1)) -> List[Tuple[str, str, int]]:
    cursor = conn.cursor()
    # Basic relevance assumption: If the query string exists in the page content
    cursor.execute(
        "SELECT url, origin, depth FROM pages WHERE content LIKE ? LIMIT 50", 
        (f"%{query.lower()}%",)
    )
    results = cursor.fetchall()
    return results # Returns list of (relevant_url, origin_url, depth)

@app.get("/status")
async def get_status():
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM pages")
    total_indexed = cursor.fetchone()[0]
    return {
        "is_crawling": state.is_crawling,
        "queue_depth": state.queue.qsize(),
        "active_workers": state.active_workers,
        "total_indexed_pages": total_indexed,
        "backpressure_status": "HIGH LOAD" if state.queue.qsize() >= MAX_QUEUE_SIZE * 0.8 else "NORMAL"
    }

# --- Minimal UI Dashboard ---
@app.get("/", response_class=HTMLResponse)
async def ui():
    return FileResponse("index.html")