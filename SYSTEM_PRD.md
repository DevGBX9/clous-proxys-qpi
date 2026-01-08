# Project Requirements Document (PRD): High-Performance Proxy Management System

## 1. Executive Summary
The **High-Performance Proxy Management System** (Ver 5.0) is an asynchronous Python-based solution designed to fetch, validate, maintain, and promote high-quality public proxies. It uses Firebase Realtime Database as a centralized storage and ScrapeProxy V4 as its primary source.

## 2. System Architecture
The system operates on an **Asynchronous Triple-Loop Architecture** using `asyncio` and `aiohttp`.

### 2.1 Component Loops:
1.  **Fetcher Loop**: Polls the ScrapeProxy API every 20 seconds for up to 2000 proxies. It pre-validates each proxy against `httpbin.org/ip` with a 10s timeout before adding it to the database.
2.  **Cleanup Loop**: Continuously scans the entire Firebase `/proxies` path every 2 seconds. It removes any proxy that fails a single health check immediately to ensure the database stays "clean."
3.  **Stability Monitor Loop**: Scans the `/proxies` path every 30 seconds. Proxies that have survived for more than **10 minutes** (600 seconds) are automatically promoted (copied) to the `/stable_proxies` path.

## 3. Data Schema (Firebase Realtime Database)

### 3.1 Main Pool (`/proxies`)
Stored as unique objects with auto-generated Firebase keys:
- `address`: String (e.g., `"1.2.3.4:8080"`)
- `created_at`: Float (Unix timestamp of first discovery)
- `last_checked`: Float (Unix timestamp of last successful validation)
- `status`: String (`"active"`)

### 3.2 Stable Pool (`/stable_proxies`)
Promoted proxies that have proven long-term reliability:
- `address`: String
- `promoted_at`: Float (Unix timestamp of promotion)
- `original_key`: String (The key from the main pool)
- `age_seconds`: Float (Lifespan at the time of promotion)

## 4. Integration Guide for AI Agents

To interact with this system, an AI or external script simply needs to fetch the JSON from one of the Firebase endpoints.

### 4.1 Endpoints
- **Full Pool**: `https://clous-proxys-qpi-default-rtdb.firebaseio.com/proxies.json`
- **Stable Pool (Recommended)**: `https://clous-proxys-qpi-default-rtdb.firebaseio.com/stable_proxies.json`

### 4.2 Code Example: Fetching a Random Stable Proxy (Python)
```python
import requests
import random

def get_random_stable_proxy():
    url = "https://clous-proxys-qpi-default-rtdb.firebaseio.com/stable_proxies.json"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        if data:
            # Extract list of addresses
            proxies = [val['address'] for val in data.values()]
            return random.choice(proxies)
    return None

# Usage
proxy = get_random_stable_proxy()
print(f"Using proxy: {proxy}")
```

## 5. Technical Specifications
- **Language**: Python 3.8+
- **Primary Library**: `aiohttp` (Asynchronous HTTP)
- **Concurrency**: Managed via `asyncio.Semaphore` (Limit: 200)
- **Validation Target**: `http://httpbin.org/ip`
- **Timeout**: 10 seconds (Balance between speed and allowing "heavy" proxies).

## 6. Maintenance and Scalability
The system is fully self-healing for both pools:
- **Main Pool Maintenance**: The **Cleanup Loop** removes dead proxies every 2 seconds.
- **Stable Pool Maintenance**: The **Cleanup Loop** also monitors the `/stable_proxies` path, ensuring that if a previously stable proxy dies, it is removed immediately from the VIP list.
- **Scalability**: To scale, increase `CONCURRENCY_LIMIT` if the host machine has high network throughput capability.
