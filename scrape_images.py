import os
import re
import time
import hashlib
import threading
import uuid
import requests
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------- CONFIG ----------------
SCRAPE_URL = "https://piixes.com/"
IMAGE_URL = "https://piixes.com/api/icon/512/{}.png"
SCRAPE_BODY = "[18]"
SLUG_REGEX = re.compile(r"api/icon/128/([a-z0-9\-]+)\.png")

MAX_WORKERS = 8
TIMEOUT = 20
MAX_RETRIES = 4
BACKOFF_BASE = 1.5
CHECKPOINT_INTERVAL = 5  # Save every N new slugs

# Optional: Hard-code cookie to skip manual input
# Leave as empty string to be prompted each run
HARDCODED_COOKIE = ""
# ----------------------------------------

session = requests.Session()
lock = threading.Lock()

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def atomic_write(path: str, content: str | bytes) -> None:
    """Publish flushed bytes from an exclusively created sibling file."""
    tmp_path = path + "." + uuid.uuid4().hex + ".tmp"
    payload = content.encode("utf-8") if isinstance(content, str) else content
    created = False
    try:
        with open(tmp_path, "xb") as handle:
            created = True
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        # Only remove this call's unpublished temporary file, never an output.
        if created and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass  # A failed cleanup must not hide the original I/O error.


def record_completion(path: str, slug: str) -> None:
    """Add one completion atomically while retaining every previous byte."""
    try:
        with open(path, "rb") as handle:
            previous = handle.read()
    except FileNotFoundError:
        previous = b""
    entry = slug.encode("ascii")
    if entry in previous.splitlines():
        return
    separator = b"\n" if previous and not previous.endswith(b"\n") else b""
    atomic_write(path, previous + separator + entry + b"\n")


def image_path(slug: str, out_dir: str) -> str:
    return os.path.join(out_dir, "piixes.com", "api", "icon", "512", f"{slug}.png")

def load_checkpoint(path):
    """Load slugs from checkpoint file."""
    if not os.path.exists(path):
        return set()
    with open(path, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_checkpoint(path, slugs):
    """Save slugs to checkpoint file atomically."""
    atomic_write(path, "\n".join(sorted(slugs)) + "\n")

def scrape_slugs(headers, checkpoint_path):
    slugs = load_checkpoint(checkpoint_path)
    initial_count = len(slugs)
    
    if initial_count > 0:
        print(f"[scrape] Loaded {initial_count} slugs from checkpoint")
    
    rounds = 0
    new_since_save = 0

    while True:
        rounds += 1
        r = session.post(
            SCRAPE_URL,
            headers=headers,
            data=SCRAPE_BODY,
            timeout=TIMEOUT
        )

        found = 0
        for slug in SLUG_REGEX.findall(r.text):
            if slug not in slugs:
                slugs.add(slug)
                found += 1
                new_since_save += 1

        print(f"[scrape] round {rounds}: +{found}, total {len(slugs)}")

        # Checkpoint every N new slugs
        if new_since_save >= CHECKPOINT_INTERVAL:
            with lock:
                save_checkpoint(checkpoint_path, slugs)
                print(f"[scrape] ✓ Checkpoint saved ({len(slugs)} slugs)")
            new_since_save = 0

        if found == 0:
            break

        time.sleep(0.6)

    # Final save
    with lock:
        save_checkpoint(checkpoint_path, slugs)
        print(f"[scrape] ✓ Final checkpoint saved ({len(slugs)} slugs)")

    return list(slugs)

def download(slug: str, out_dir: str, seen_hashes: set[str], completed_path: str) -> str:
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        return "fail"
    path = image_path(slug, out_dir)
    payload = None
    published_here = False
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if payload is None:
                response = session.get(IMAGE_URL.format(slug), timeout=TIMEOUT)
                if response.status_code != 200:
                    raise requests.HTTPError("Image endpoint returned a non-200 response")
                payload = response.content
            digest = sha256(payload)

            # Serialize local publication only; network requests stay concurrent.
            with lock:
                existed = os.path.exists(path)
                if existed:
                    with open(path, "rb") as handle:
                        if handle.read() != payload:
                            return "fail"  # Preserve older or partial bytes for review.
                else:
                    atomic_write(path, payload)
                    published_here = True
                record_completion(completed_path, slug)
                result = "skip" if existed and not published_here else (
                    "dedup" if digest in seen_hashes else "ok"
                )
                seen_hashes.add(digest)
                return result

        except (OSError, requests.RequestException):
            if attempt == MAX_RETRIES:
                return "fail"
            time.sleep(BACKOFF_BASE ** attempt)
    return "fail"

def main():
    out_dir = input("Output folder path: ").strip()
    os.makedirs(out_dir, exist_ok=True)

    checkpoint_path = os.path.join(out_dir, "slugs_checkpoint.txt")
    completed_path = os.path.join(out_dir, "completed_downloads.txt")
    failed_path = os.path.join(out_dir, "failed_downloads.txt")

    # Use hard-coded cookie or prompt user
    if HARDCODED_COOKIE:
        cookie = HARDCODED_COOKIE
        print("\n[+] Using hard-coded cookie from config")
    else:
        print("\n" + "="*70)
        print("HOW TO GET YOUR COOKIE:")
        print("="*70)
        print("1. Open https://piixes.com in your browser")
        print("2. Press F12 to open Developer Tools")
        print("3. Go to the 'Network' tab")
        print("4. Refresh the page (F5)")
        print("5. Click on any request to piixes.com")
        print("6. Find 'Cookie:' in Request Headers section")
        print("7. Copy the entire cookie value (everything after 'Cookie: ')")
        print("="*70)
        print()
        cookie = input("Paste Cookie header value: ").strip()

    headers = {
        "accept": "text/x-component",
        "content-type": "text/plain;charset=UTF-8",
        "origin": "https://piixes.com",
        "referer": "https://piixes.com/",
        "user-agent": "Mozilla/5.0",
        "cookie": cookie
    }

    print("\n[+] Scraping slugs…")
    slugs = scrape_slugs(headers, checkpoint_path)
    print(f"[+] Total slugs discovered: {len(slugs)}\n")

    # Load already completed downloads
    completed = load_checkpoint(completed_path)
    if completed:
        print(f"[+] Found {len(completed)} previously completed downloads\n")
    
    # A completion entry without its image must be retried.
    slugs_to_download = [
        slug for slug in slugs
        if slug not in completed
        or not re.fullmatch(r"[a-z0-9-]+", slug)
        or not os.path.isfile(image_path(slug, out_dir))
    ]
    
    if not slugs_to_download:
        print("[+] All downloads already completed!")
        return

    print(f"[+] Downloading {len(slugs_to_download)} PNGs…")

    seen_hashes = set()
    results = {"ok": 0, "skip": 0, "dedup": 0, "fail": 0}
    failed_slugs = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(download, slug, out_dir, seen_hashes, completed_path): slug
            for slug in slugs_to_download
        }

        for future in tqdm(as_completed(futures), total=len(futures)):
            slug = futures[future]
            status = future.result()
            
            with lock:
                results[status] += 1
                if status == "fail":
                    failed_slugs.append(slug)

    # Save failed downloads for manual retry
    if failed_slugs:
        atomic_write(failed_path, "\n".join(failed_slugs) + "\n")
        print(f"\n[!] {len(failed_slugs)} failed downloads saved to {failed_path}")

    print("\nDone.")
    for k, v in results.items():
        print(f"{k:>6}: {v}")

if __name__ == "__main__":
    main()
