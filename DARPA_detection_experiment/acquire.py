"""Проверка доступа и загрузка полных файлов MIT. Частичные файлы не считаются данными."""

import argparse
import concurrent.futures
import hashlib
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = "https://archive.ll.mit.edu/ideval/data/2000/LLS_DDOS_1.0/data_and_labeling/"
FILES = {
    "inside_full.dump.gz": BASE + "tcpdump_inside/LLS_DDOS_1.0-inside.dump.gz",
    **{f"phase-{i}-dump": BASE + f"tcpdump_inside/phase-{i}-dump" for i in range(1, 6)},
}


def probe(url):
    start = time.monotonic()
    try:
        request = urllib.request.Request(url, headers={"Range": "bytes=0-31"})
        with urllib.request.urlopen(request, timeout=15) as response:
            return {
                "url": url,
                "status": response.status,
                "headers": dict(response.headers),
                "magic": response.read(32).hex(),
                "seconds": time.monotonic() - start,
            }
    except Exception as error:
        return {"url": url, "error": str(error), "seconds": time.monotonic() - start}


def download(name, url):
    path = ROOT / "data/raw" / name
    if not path.exists():
        temp = path.with_suffix(path.suffix + ".part")
        request = urllib.request.Request(
            url, headers={"User-Agent": "DARPA-research-download/1.0"}
        )
        print(f"Download: {name}", flush=True)
        with urllib.request.urlopen(request, timeout=45) as response, temp.open(
            "wb"
        ) as output:
            expected = response.headers.get("Content-Length")
            total = 0
            last = time.monotonic()
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                total += len(chunk)
                if time.monotonic() - last > 10:
                    print(f"{name}: {total:,} bytes", flush=True)
                    last = time.monotonic()
        if expected and total != int(expected):
            raise ValueError(f"Incomplete {name}: {total} / {expected}")
        temp.replace(path)
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return {
        "file": name,
        "url": url,
        "bytes": path.stat().st_size,
        "sha256": digest,
        "hash_origin": "local integrity, not an MIT reference checksum",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    (ROOT / "data/raw").mkdir(parents=True, exist_ok=True)
    if args.probe:
        urls = [
            FILES["inside_full.dump.gz"],
            FILES["phase-1-dump"],
            BASE + "LLS_DDOS_1.0.tar.gz",
        ]
        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            results = list(pool.map(probe, urls))
        (ROOT / "data/access_check.json").write_text(json.dumps(results, indent=2), "utf-8")
        print(json.dumps(results, indent=2), flush=True)
    else:
        manifest = []
        for name, url in FILES.items():
            manifest.append(download(name, url))
            (ROOT / "data/manifest.json").write_text(
                json.dumps(manifest, indent=2), "utf-8"
            )


if __name__ == "__main__":
    main()
