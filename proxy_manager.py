import asyncio
import aiohttp
import time
import logging
import json

# Configuration
PROXY_API_URL = "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all&skip=0&limit=2000"
FIREBASE_BASE_URL = "https://clous-proxys-qpi-default-rtdb.firebaseio.com/proxies"
STABLE_PROXIES_URL = "https://clous-proxys-qpi-default-rtdb.firebaseio.com/stable_proxies"
CHECK_URL = "http://httpbin.org/ip"

# Performance Tuning (Async Mode)
CONCURRENCY_LIMIT = 200      # Max concurrent requests
HEALTH_CHECK_TIMEOUT = 10     # Relaxed timeout for accuracy
FETCH_INTERVAL = 20           # Seconds between bulk fetch cycles
CLEANUP_INTERVAL = 2          # Minimal delay between cleanup cycles
STABILITY_CHECK_INTERVAL = 30 # Seconds between stability scans
STABILITY_THRESHOLD = 600    # 10 minutes (in seconds) to be considered stable

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

async def is_proxy_alive(session, proxy_addr):
    """Asynchronously checks if a proxy is functional."""
    proxy_url = f"http://{proxy_addr}"
    try:
        async with session.get(CHECK_URL, proxy=proxy_url, timeout=HEALTH_CHECK_TIMEOUT) as response:
            return response.status == 200
    except:
        return False

async def get_existing_proxies(session, url):
    """Fetches all proxy addresses from a given Firebase URL asynchronously."""
    try:
        async with session.get(f"{url}.json") as response:
            if response.status == 200:
                data = await response.json()
                if data:
                    return {val.get("address") for val in data.values() if val.get("address")}
        return set()
    except Exception as e:
        logging.error(f"Firebase read error for {url}: {e}")
        return set()

async def validate_and_add(session, semaphore, proxy_addr, existing_proxies, lock):
    """Validates and adds working unique proxies asynchronously."""
    async with semaphore:
        proxy_addr = proxy_addr.strip()
        if not proxy_addr: return
            
        async with lock:
            if proxy_addr in existing_proxies: return
        
        if await is_proxy_alive(session, proxy_addr):
            proxy_data = {
                "address": proxy_addr, 
                "last_checked": time.time(), 
                "created_at": time.time(), # Adding creation timestamp
                "status": "active"
            }
            try:
                async with session.post(f"{FIREBASE_BASE_URL}.json", json=proxy_data) as res:
                    if res.status == 200:
                        logging.info(f"[+] ADDED: {proxy_addr}")
                        async with lock:
                            existing_proxies.add(proxy_addr)
            except Exception as e:
                logging.error(f"Error adding {proxy_addr}: {e}")

async def fetch_and_store_loop(session):
    """Async loop for bulk fetching proxies."""
    logging.info("Starting Async Bulk Fetcher...")
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    lock = asyncio.Lock()
    
    while True:
        try:
            async with session.get(PROXY_API_URL, timeout=15) as response:
                if response.status == 200:
                    text = await response.text()
                    proxies = text.strip().split('\n')
                    logging.info(f"API provided {len(proxies)} proxies. Validating...")
                    
                    existing = await get_existing_proxies(session, FIREBASE_BASE_URL)
                    
                    tasks = [validate_and_add(session, semaphore, p, existing, lock) for p in proxies]
                    await asyncio.gather(*tasks)
                    logging.info("Fetch & Validation cycle complete.")
                else:
                    logging.error(f"API Error: {response.status}")
        except Exception as e:
            logging.error(f"Fetcher loop error: {e}")
        
        await asyncio.sleep(FETCH_INTERVAL)

async def check_and_delete(session, semaphore, key, proxy_addr):
    """Immediate cleanup: Removes proxy if it fails once."""
    async with semaphore:
        if not await is_proxy_alive(session, proxy_addr):
            try:
                async with session.delete(f"{FIREBASE_BASE_URL}/{key}.json") as res:
                    if res.status == 200:
                        logging.warning(f"[-] REMOVED: {proxy_addr}")
            except Exception as e:
                logging.error(f"Delete error for {proxy_addr}: {e}")

async def cleanup_loop(session):
    """Async loop for continuous highly-parallel cleanup."""
    logging.info("Starting Continuous Async Cleanup...")
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    
    while True:
        try:
            async with session.get(f"{FIREBASE_BASE_URL}.json") as response:
                if response.status == 200:
                    data = await response.json()
                    if data:
                        logging.info(f"Scanning {len(data)} proxies in DB...")
                        tasks = [check_and_delete(session, semaphore, key, val.get("address")) 
                                 for key, val in data.items() if val.get("address")]
                        await asyncio.gather(*tasks)
                    else:
                        logging.debug("DB is empty.")
                else:
                    logging.error(f"Firebase fetch error: {response.status}")
        except Exception as e:
            logging.error(f"Cleanup loop error: {e}")
            
        await asyncio.sleep(CLEANUP_INTERVAL)

async def stability_monitor_loop(session):
    """Async loop to promote long-lived proxies to stable database."""
    logging.info("Starting Stability Monitor Loop...")
    
    while True:
        try:
            async with session.get(f"{FIREBASE_BASE_URL}.json") as response:
                if response.status == 200:
                    data = await response.json()
                    if data:
                        current_time = time.time()
                        # Get those already in stable to avoid double-posting
                        stable_existing = await get_existing_proxies(session, STABLE_PROXIES_URL)
                        
                        for key, val in data.items():
                            p_addr = val.get("address")
                            created_at = val.get("created_at", current_time) # Default to now if missing
                            
                            age = current_time - created_at
                            if age >= STABILITY_THRESHOLD and p_addr not in stable_existing:
                                # Promote to stable
                                stable_data = {
                                    "address": p_addr,
                                    "promoted_at": current_time,
                                    "original_key": key,
                                    "age_seconds": age
                                }
                                async with session.post(f"{STABLE_PROXIES_URL}.json", json=stable_data) as res:
                                    if res.status == 200:
                                        logging.info(f"[★] PROMOTED to stable: {p_addr} (Age: {int(age)}s)")
                                        stable_existing.add(p_addr)
                else:
                    logging.error(f"Firebase (Stability) fetch error: {response.status}")
        except Exception as e:
            logging.error(f"Stability monitor error: {e}")
            
        await asyncio.sleep(STABILITY_CHECK_INTERVAL)

async def main():
    async with aiohttp.ClientSession() as session:
        logging.info("!!! ASYNC PROXY MANAGER (VER 5.0) STARTED !!!")
        
        # Run all three loops concurrently
        await asyncio.gather(
            fetch_and_store_loop(session),
            cleanup_loop(session),
            stability_monitor_loop(session)
        )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Shutting down...")
