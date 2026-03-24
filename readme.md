# Brightwave Web Crawler & Search Engine

A lightweight, multi-threaded web crawler and search engine built entirely with Python standard libraries. It features an integrated HTTP server, file-based inverted indexing, and a live tracking dashboard.

## Features
- **Zero Dependencies:** Runs on pure Python 3. No external packages required.
- **Multi-Threading:** Spawns background worker threads for non-blocking UI interactions.
- **Inverted Indexing:** Stores parsed word frequencies in alphabetized JSON partitions (`a.data`, `b.data`) for fast, localized search lookups.
- **Live UI:** A single-page application (SPA) dashboard to start crawls, monitor active thread queues, and execute searches.

## Prerequisites
- Python 3.8 or higher.
- No `pip install` commands are needed.

## Installation & Running
1. Clone this repository to your local machine.
2. Open a terminal and navigate to the project directory.
3. Run the main Python script:
   ```bash
   python3 crawler.py