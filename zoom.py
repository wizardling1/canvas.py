#!/usr/bin/env python3
import argparse, os, sys, urllib.parse, pathlib, re, json
from datetime import datetime
from typing import Optional, Tuple

from canvascli.parsing import HelpfulArgumentParser

# ------------------ helpers ------------------

def load_cookies(browser: str, domains=("zoom.us", "wsu.zoom.us")):
    import browser_cookie3 as bc3

    get = {"chrome": bc3.chrome, "safari": bc3.safari, "firefox": bc3.firefox}[browser]
    jar = get(domain_name=None)
    cookies = []
    wanted = {d.lstrip(".") for d in domains}
    for c in jar:
        dom = (c.domain or "").lstrip(".")
        if any(dom.endswith(w) for w in wanted):
            cd = {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,             # keep leading dot if present
                "path": c.path or "/",
                "httpOnly": bool(getattr(c, "rest", {}).get("HttpOnly", False)),
                "secure": bool(c.secure),
            }
            if c.expires:
                cd["expires"] = int(c.expires)
            cookies.append(cd)
    return cookies

def slugify(name: str, maxlen: int = 120) -> str:
    name = name.strip()
    # collapse whitespace & illegal chars
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"[^A-Za-z0-9 _\-.()+]", "", name)
    name = name.replace(" ", "_")
    return name[:maxlen].strip("_") or "zoom_recording"

def safe_filename_from_url(url: str, fallback="transcript"):
    p = urllib.parse.urlparse(url)
    base = pathlib.Path(p.path).name or fallback
    q = urllib.parse.parse_qs(p.query)
    bits = []
    for k in ("lang", "track", "id", "fid", "type"):
        if k in q and q[k]:
            bits.append(f"{k}-{q[k][0]}")
    if bits:
        stem, ext = os.path.splitext(base)
        base = f"{stem}__{'_'.join(bits)}{ext}"
    return re.sub(r"[^A-Za-z0-9_.\-]+", "_", base)

def vtt_bytes_to_txt(vtt_bytes: bytes) -> str:
    text_lines = []
    for raw in vtt_bytes.decode("utf-8", errors="ignore").splitlines():
        line = raw.strip("\ufeff").strip()
        if not line:
            continue
        # skip headers, indices, timecodes
        if line.upper().startswith("WEBVTT"):
            continue
        if re.match(r"^\d+$", line):
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}", line):
            continue
        # remove styling tags if present
        line = re.sub(r"</?[\w.:#=-]+>", "", line)
        text_lines.append(line)
    # dedupe consecutive duplicates (common in VTTs)
    out = []
    prev = None
    for ln in text_lines:
        if ln != prev:
            out.append(ln)
        prev = ln
    return "\n".join(out)

def json_to_vtt_and_txt(json_bytes: bytes) -> Tuple[bytes, bytes]:
    data = json.loads(json_bytes.decode("utf-8", errors="ignore"))
    # Support common Zoom shapes
    if "result" in data and isinstance(data["result"], list):
        entries = data["result"]
    elif "events" in data and isinstance(data["events"], list):
        entries = []
        for ev in data["events"]:
            start = ev.get("ts") or ev.get("start") or 0
            dur = ev.get("duration") or 1.5
            text = ev.get("text") or ev.get("payload", {}).get("text") or ""
            entries.append({"start": start, "end": start + dur, "text": text})
    else:
        # Fallback: try a flat list
        if isinstance(data, list):
            entries = data
        else:
            raise ValueError("Unrecognized transcript JSON structure.")

    def ts(sec: float) -> str:
        ms = int(float(sec) * 1000)
        hh = ms // 3600000
        mm = (ms // 60000) % 60
        ss = (ms // 1000) % 60
        mmm = ms % 1000
        return f"{hh:02d}:{mm:02d}:{ss:02d}.{mmm:03d}"

    vtt_lines = ["WEBVTT", ""]
    txt_lines = []
    idx = 1
    for e in entries:
        start = e.get("start", 0)
        end = e.get("end", start + 1.5)
        txt = (e.get("text") or "").strip()
        if not txt:
            continue
        txt = re.sub(r"\s+", " ", txt)
        vtt_lines.append(str(idx))
        vtt_lines.append(f"{ts(start)} --> {ts(end)}")
        vtt_lines.append(txt)
        vtt_lines.append("")
        txt_lines.append(txt)
        idx += 1

    vtt_b = ("\n".join(vtt_lines)).encode("utf-8")
    txt_b = ("\n".join(txt_lines)).encode("utf-8")
    return vtt_b, txt_b

# ------------------ core ------------------

def process_one_url(playwright, args, url: str, outdir: pathlib.Path) -> bool:
    """
    Returns True if something was saved; False otherwise.
    """
    browser = playwright.chromium.launch(headless=not args.headful)
    context = browser.new_context(user_agent=(
        "Mozilla/5.0 (Macintosh; arm64) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17 Safari/605.1.15"
    ))

    # Load cookies
    try:
        cookies = load_cookies(args.browser)
        if cookies:
            context.add_cookies(cookies)
            print(f"[info] Loaded {len(cookies)} {args.browser} cookies for zoom domains.")
        else:
            print(f"[warn] No {args.browser} cookies for zoom; you may hit an auth wall.")
    except Exception as e:
        print(f"[warn] Could not load {args.browser} cookies: {e}")

    page = context.new_page()

    # Will be set after navigation
    current_title = "zoom_recording"

    # Track if we saved anything
    saved_any = False
    saved_paths = []

    def descriptive_stem(default_stem: Optional[str] = None) -> str:
        base = slugify(current_title)
        if default_stem:
            # attach a small hint (like lang/fid) if useful
            base = f"{base}__{default_stem}"
        return base

    def maybe_save(resp):
        nonlocal saved_any, saved_paths
        url_ = resp.url
        ctype = (resp.headers.get("content-type") or "").lower()

        # Grab body first; we’ll sniff the bytes for WEBVTT regardless of headers.
        try:
            body = resp.body()  # bytes
        except Exception:
            return

        # ---- Robust VTT detection ----
        # Treat as VTT if:
        #  - header/URL says so, OR
        #  - the payload starts with WEBVTT (common Zoom mislabel)
        url_says_vtt = (".vtt" in url_.lower())
        header_says_vtt = ("text/vtt" in ctype)
        bytes_look_vtt = body.lstrip().startswith(b"WEBVTT")

        looks_transcriptish = (
            url_says_vtt or header_says_vtt or bytes_look_vtt or
            "transcript" in url_.lower() or
            "caption" in url_.lower() or
            "subtitle" in url_.lower()
        )
        if not looks_transcriptish:
            return

        def stem_from_title(hint: str | None = None) -> str:
            parsed_hint = slugify(os.path.splitext(safe_filename_from_url(url_))[0], 60)
            base = slugify(current_title)
            if hint:
                return f"{base}__{hint}"
            return f"{base}__{parsed_hint}"

        # If it *is* or *looks like* VTT, save VTT+TXT
        if url_says_vtt or header_says_vtt or bytes_look_vtt:
            stem = stem_from_title(None)
            vtt_path = outdir / f"{stem}.vtt"
            vtt_path.write_bytes(body)

            # Convert to TXT
            txt = vtt_bytes_to_txt(body).encode("utf-8")
            txt_path = outdir / f"{stem}.txt"
            txt_path.write_bytes(txt)

            print(f"[save] {vtt_path}")
            print(f"[save] {txt_path}")
            saved_any = True
            saved_paths += [vtt_path, txt_path]
            return

        # Otherwise try JSON → VTT/TXT
        try:
            vtt_b, txt_b = json_to_vtt_and_txt(body)
            stem = stem_from_title(None)
            vtt_path = outdir / f"{stem}.vtt"
            txt_path = outdir / f"{stem}.txt"
            vtt_path.write_bytes(vtt_b)
            txt_path.write_bytes(txt_b)
            print(f"[save] {vtt_path}")
            print(f"[save] {txt_path}")
            saved_any = True
            saved_paths += [vtt_path, txt_path]
        except Exception as e:
            # Last-resort raw save for debugging
            raw_path = outdir / f"{stem_from_title('raw_transcript')}.bin"
            raw_path.write_bytes(body)
            print(f"[warn] Unknown transcript format; saved raw to {raw_path} ({e})")

    def log_request(req):
        if args.verbose:
            print(f"[req] {req.method} {req.url}")

    def log_response(resp):
        url_ = resp.url
        ctype = (resp.headers.get("content-type") or "").lower()
        if args.verbose and (".vtt" in url_.lower() or "transcript" in url_.lower() or "text/vtt" in ctype):
            print(f"[resp] {resp.status} {ctype} {url_}")
        maybe_save(resp)

    page.on("request", log_request)
    page.on("response", log_response)

    print(f"[info] Navigating to {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=90_000)

    # Capture title for descriptive filenames
    try:
        current_title = page.title() or "zoom_recording"
    except Exception:
        current_title = "zoom_recording"

    # Try to expand transcript
    selectors = [
        "text='Audio Transcript'",
        "button:has-text('Audio Transcript')",
        "[role=button]:has-text('Audio Transcript')",
        "text='Captions'",
        "button:has-text('Captions')",
        "[data-qa='transcript']",
    ]
    clicked = False
    for sel in selectors:
        try:
            # Short wait; if it’s not there, move on rather than hanging
            page.wait_for_selector(sel, state="visible", timeout=2000)
            page.click(sel, timeout=1000)
            print(f"[info] Clicked {sel}")
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        print("[warn] Transcript/Captions control not found quickly; continuing anyway.")

    # Give network time to load transcript calls
    print("[info] Waiting for transcript network calls…")
    page.wait_for_timeout(8000)
    try:
        page.mouse.wheel(0, 2000)
        page.wait_for_timeout(4000)
    except Exception:
        pass

    if not saved_any:
        print("\nNo transcript found.")
        print("- Ensure the cloud recording has 'Audio Transcript' enabled.")
        print("- Open the recording in Chrome, log in, expand the transcript manually once, then rerun with --browser chrome.")
        print("- If your school uses SSO, make sure your cookies include both wsu.zoom.us and zoom.us.")

    context.close()
    browser.close()
    return saved_any

# ------------------ CLI ------------------

def _parser() -> argparse.ArgumentParser:
    parser = HelpfulArgumentParser(
        description=(
            "Open Zoom cloud recordings with imported browser cookies, capture "
            "transcript responses, and save VTT and plain-text files."
        ),
        epilog=(
            "examples:\n"
            "  python3 zoom.py 'https://example.zoom.us/rec/play/...'\n"
            "  python3 zoom.py --browser firefox --headful\n"
            "  python3 zoom.py --outdir ./transcripts"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "url",
        nargs="?",
        metavar="URL",
        help="Zoom cloud-recording play URL; prompts interactively when omitted",
    )
    parser.add_argument(
        "--browser",
        choices=["chrome", "safari", "firefox"],
        default="chrome",
        help="Browser profile from which to import Zoom cookies (default: chrome)",
    )
    parser.add_argument(
        "--outdir",
        type=pathlib.Path,
        default=pathlib.Path("downloads"),
        metavar="DIR",
        help="Directory for VTT and text files (default: ./downloads)",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Show the automated browser window instead of running headless",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print matching network requests"
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)

    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Zoom support is not installed; run: pip install -e '.[zoom]'"
        ) from exc

    outdir = args.outdir.expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        # process initial URL or prompt for one
        this_url = args.url
        if not this_url:
            this_url = input("Paste Zoom play URL (or blank to exit): ").strip()
            if not this_url:
                print("Bye.")
                return

        while True:
            _ = process_one_url(pw, args, this_url, outdir)

            # Ask whether to continue
            try:
                ans = input("\nProcess another Zoom recording? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break

            if ans not in ("y", "yes"):
                print("Bye.")
                break

            # Ask for next URL
            try:
                nxt = input("Paste next Zoom play URL: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break
            if not nxt:
                print("Bye.")
                break
            this_url = nxt

if __name__ == "__main__":
    main()
