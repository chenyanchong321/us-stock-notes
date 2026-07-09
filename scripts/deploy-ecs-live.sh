#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

SITE_DIR="/var/www/us-stock"
DOMAIN="stock.ziyuanai.top"
REPO_TARBALL_URL="https://codeload.github.com/chenyanchong321/us-stock-notes/tar.gz/refs/heads/main"

echo "== packages =="
apt-get update -qq
apt-get install -y -qq nginx curl python3 ca-certificates cron rsync tar gzip

echo "== sync helper =="
cat > /root/us-stock-sync.sh <<'SYNC'
#!/usr/bin/env bash
set -euo pipefail

URL="https://codeload.github.com/chenyanchong321/us-stock-notes/tar.gz/refs/heads/main"
SITE="/var/www/us-stock"
TMP="$(mktemp -d)"
KEEP_LIVE="$TMP/live.json"

cleanup() {
  rm -rf "$TMP"
}
trap cleanup EXIT

mkdir -p "$SITE/data"
if [ -f "$SITE/data/live.json" ]; then
  cp "$SITE/data/live.json" "$KEEP_LIVE"
fi

curl -fsSL --retry 3 --connect-timeout 10 --max-time 90 "$URL" -o "$TMP/site.tgz"
mkdir -p "$TMP/site"
tar -xzf "$TMP/site.tgz" -C "$TMP/site" --strip-components=1

rsync -a --delete \
  --exclude '/data/live.json' \
  --exclude '/.well-known/' \
  "$TMP/site/" "$SITE/"

mkdir -p "$SITE/data"
if [ -f "$KEEP_LIVE" ]; then
  cp "$KEEP_LIVE" "$SITE/data/live.json"
fi

chown -R www-data:www-data "$SITE"
find "$SITE" -type d -exec chmod 755 {} +
find "$SITE" -type f -exec chmod 644 {} +
echo "SYNC_OK $(date -Is)"
SYNC
chmod 700 /root/us-stock-sync.sh

echo "== sync site =="
/root/us-stock-sync.sh

echo "== nginx =="
install -d /etc/nginx/sites-available /etc/nginx/sites-enabled
if [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ] \
   && [ -f "/etc/letsencrypt/live/$DOMAIN/privkey.pem" ] \
   && [ -f /etc/letsencrypt/options-ssl-nginx.conf ] \
   && [ -f /etc/letsencrypt/ssl-dhparams.pem ]; then
  cat > /etc/nginx/sites-available/us-stock <<NGINX
server {
    listen 80;
    server_name $DOMAIN 118.31.109.150;
    return 301 https://$DOMAIN\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name $DOMAIN;
    root $SITE_DIR;
    index index.html;

    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    location / {
        try_files \$uri \$uri/ =404;
    }

    location = /data/live.json {
        add_header Cache-Control "no-store" always;
        try_files \$uri =404;
    }
}
NGINX
else
  cat > /etc/nginx/sites-available/us-stock <<NGINX
server {
    listen 80;
    server_name $DOMAIN 118.31.109.150;
    root $SITE_DIR;
    index index.html;

    location / {
        try_files \$uri \$uri/ =404;
    }

    location = /data/live.json {
        add_header Cache-Control "no-store" always;
        try_files \$uri =404;
    }
}
NGINX
fi
ln -sf /etc/nginx/sites-available/us-stock /etc/nginx/sites-enabled/us-stock
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable nginx >/dev/null
systemctl restart nginx

echo "== live quote script =="
cat > /root/us-live.py <<'PY'
#!/usr/bin/env python3
import concurrent.futures
import http.cookiejar
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
NASDAQ_HEADERS = {
    "User-Agent": UA["User-Agent"],
    "Accept": "application/json,text/plain,*/*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}
BATCH_SIZE = 40
MIN_QUOTES_TO_WRITE = 20
NASDAQ_WORKERS = int(os.environ.get("NASDAQ_WORKERS", "8"))

site_dir = pathlib.Path(os.environ.get("US_STOCK_SITE_DIR", "/var/www/us-stock"))
out = site_dir / "data" / "live.json"
watchlist = site_dir / "config" / "watchlist.json"
crumb_file = pathlib.Path(os.environ.get("YAHOO_CRUMB_FILE", "/root/.yahoo_crumb"))
cookie_file = pathlib.Path(os.environ.get("YAHOO_COOKIE_FILE", "/root/.yahoo_cookies.txt"))


class AuthError(Exception):
    pass


def make_opener(jar):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = list(UA.items())
    return opener


def chmod_private(path):
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def refresh_auth():
    crumb_file.parent.mkdir(parents=True, exist_ok=True)
    cookie_file.parent.mkdir(parents=True, exist_ok=True)
    jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
    opener = make_opener(jar)
    try:
        opener.open("https://fc.yahoo.com", timeout=15).read(0)
    except Exception:
        pass
    crumb = opener.open("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=15).read().decode().strip()
    if not crumb or "<" in crumb or len(crumb) > 200:
        raise RuntimeError("invalid Yahoo crumb")
    crumb_file.write_text(crumb + "\n", encoding="utf-8")
    chmod_private(crumb_file)
    jar.save(ignore_discard=True, ignore_expires=True)
    chmod_private(cookie_file)
    return opener, crumb


def load_auth():
    jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
    if cookie_file.exists():
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except Exception:
            jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
    crumb = ""
    if crumb_file.exists():
        crumb = crumb_file.read_text(encoding="utf-8").strip()
    if crumb and len(jar) > 0:
        return make_opener(jar), crumb
    return refresh_auth()


def as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_market_number(value):
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NA", "--", "-"}:
        return None
    text = text.replace("\u2212", "-").replace(",", "")
    text = re.sub(r"[^0-9.+-]", "", text)
    if text in {"", "+", "-", ".", "+.", "-."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def pct_value(q, price, pct_key):
    pct = as_float(q.get(pct_key))
    if pct is not None:
        return pct
    prev = as_float(q.get("regularMarketPreviousClose"))
    if prev and price:
        return (price / prev - 1.0) * 100.0
    return 0.0


def nasdaq_asset_class(market):
    if "ETF" in market:
        return "etf"
    if "\u6307\u6570" in market:
        return "indexes"
    if "OTC" in market:
        return None
    return "stocks"


def nasdaq_symbol(yahoo, code):
    symbol = str(yahoo or code).strip().upper()
    if not symbol or symbol.startswith("^"):
        return None
    return symbol.replace("-", ".")


def load_symbols():
    data = json.loads(watchlist.read_text(encoding="utf-8"))
    sym_to_codes = {}
    nasdaq_targets = {}
    seen_codes = set()
    for section in data.get("sections", []):
        for item in section.get("items", []):
            market = str(item.get("market", ""))
            code = str(item.get("code", "")).strip().upper()
            if not market.startswith("\u7f8e\u80a1") or not code or code in seen_codes:
                continue
            yahoo = str(item.get("yahoo") or code).strip()
            if not yahoo:
                continue
            seen_codes.add(code)
            sym_to_codes.setdefault(yahoo, []).append(code)
            asset_class = nasdaq_asset_class(market)
            nsymbol = nasdaq_symbol(yahoo, code)
            if asset_class and nsymbol:
                nasdaq_targets.setdefault((nsymbol, asset_class), []).append(code)
    return sym_to_codes, nasdaq_targets


def fetch_chunk(opener, crumb, chunk):
    url = (
        "https://query1.finance.yahoo.com/v7/finance/quote?symbols="
        + urllib.parse.quote(",".join(chunk))
        + "&crumb="
        + urllib.parse.quote(crumb)
    )
    try:
        with opener.open(url, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read(500).decode("utf-8", errors="ignore")
        if exc.code in (401, 403) or "Unauthorized" in body:
            raise AuthError(f"Yahoo auth failed: HTTP {exc.code}") from exc
        raise


def collect_quotes(sym_to_codes, opener, crumb):
    quotes = {}
    states = {}
    symbols = list(sym_to_codes)
    for i in range(0, len(symbols), BATCH_SIZE):
        chunk = symbols[i : i + BATCH_SIZE]
        data = fetch_chunk(opener, crumb, chunk)
        for q in data.get("quoteResponse", {}).get("result", []):
            symbol = q.get("symbol")
            if not symbol:
                continue
            state = str(q.get("marketState", ""))
            states[symbol] = state
            price = None
            pct = 0.0
            if state.startswith("PRE"):
                price = as_float(q.get("preMarketPrice"))
                pct = pct_value(q, price, "preMarketChangePercent")
            elif state.startswith("POST"):
                price = as_float(q.get("postMarketPrice"))
                pct = pct_value(q, price, "postMarketChangePercent")
            if not price or price <= 0:
                continue
            for code in sym_to_codes.get(symbol, []):
                quotes[code] = {"p": round(price, 4), "c": round(pct, 2)}
        time.sleep(0.5)
    return quotes, states


def fetch_nasdaq_one(symbol, asset_class, codes):
    url = (
        "https://api.nasdaq.com/api/quote/"
        + urllib.parse.quote(symbol, safe="")
        + "/info?assetclass="
        + urllib.parse.quote(asset_class, safe="")
    )
    req = urllib.request.Request(url, headers=NASDAQ_HEADERS)
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.load(resp)
    status = data.get("status", {})
    if status.get("rCode") != 200:
        return symbol, None, None, codes, f"rCode={status.get('rCode')}"
    body = data.get("data") or {}
    market_status = str(body.get("marketStatus") or "")
    if "Pre-Market" not in market_status and "After" not in market_status:
        return symbol, market_status, None, codes, None
    primary = body.get("primaryData") or {}
    price = as_market_number(primary.get("lastSalePrice"))
    pct = as_market_number(primary.get("percentageChange"))
    if not price or price <= 0:
        return symbol, market_status, None, codes, "no price"
    if pct is None:
        pct = 0.0
    return symbol, market_status, {"p": round(price, 4), "c": round(pct, 2)}, codes, None


def collect_nasdaq_quotes(nasdaq_targets):
    quotes = {}
    states = {}
    errors = 0
    items = [(symbol, asset, codes) for (symbol, asset), codes in nasdaq_targets.items()]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, NASDAQ_WORKERS)) as pool:
        future_map = {
            pool.submit(fetch_nasdaq_one, symbol, asset, codes): (symbol, asset)
            for symbol, asset, codes in items
        }
        for future in concurrent.futures.as_completed(future_map):
            symbol, _asset = future_map[future]
            try:
                _symbol, market_status, quote, codes, error = future.result()
            except Exception:
                errors += 1
                continue
            if market_status:
                states[symbol] = market_status
            if error:
                errors += 1
                continue
            if not quote:
                continue
            for code in codes:
                quotes[code] = quote
    return quotes, states, errors


def write_live(quotes):
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(
        json.dumps({"t": int(time.time()), "q": quotes}, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    tmp.replace(out)


def state_counts(states):
    counts = {}
    for state in states.values():
        counts[state] = counts.get(state, 0) + 1
    return counts


def main():
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        sym_to_codes, nasdaq_targets = load_symbols()
    except Exception as exc:
        print(f"LIVE_FAIL watchlist: {exc}", file=sys.stderr)
        return 1
    if not sym_to_codes:
        print("LIVE_SKIP no US symbols")
        return 0

    force_nasdaq = os.environ.get("FORCE_NASDAQ_LIVE", "").lower() in {"1", "true", "yes"}
    yahoo_error = None
    if not force_nasdaq:
        try:
            opener, crumb = load_auth()
            try:
                quotes, states = collect_quotes(sym_to_codes, opener, crumb)
            except AuthError:
                opener, crumb = refresh_auth()
                quotes, states = collect_quotes(sym_to_codes, opener, crumb)
            if len(quotes) >= MIN_QUOTES_TO_WRITE:
                write_live(quotes)
                print(f"LIVE_OK yahoo quotes={len(quotes)}")
                return 0
            print(f"LIVE_WARN yahoo quotes={len(quotes)} states={state_counts(states)}; trying Nasdaq")
        except urllib.error.HTTPError as exc:
            yahoo_error = f"HTTP {exc.code}"
            print(f"LIVE_WARN yahoo {yahoo_error}; trying Nasdaq", file=sys.stderr)
        except Exception as exc:
            yahoo_error = str(exc)
            print(f"LIVE_WARN yahoo: {exc}; trying Nasdaq", file=sys.stderr)

    if not nasdaq_targets:
        print(f"LIVE_FAIL no Nasdaq fallback targets; yahoo_error={yahoo_error}", file=sys.stderr)
        return 1

    try:
        quotes, states, errors = collect_nasdaq_quotes(nasdaq_targets)
    except Exception as exc:
        print(f"LIVE_FAIL nasdaq: {exc}; keep previous live.json", file=sys.stderr)
        return 1

    if len(quotes) < MIN_QUOTES_TO_WRITE:
        print(
            f"LIVE_SKIP nasdaq quotes={len(quotes)} states={state_counts(states)} "
            f"errors={errors}; keep previous live.json"
        )
        return 0

    write_live(quotes)
    print(f"LIVE_OK nasdaq quotes={len(quotes)} errors={errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
PY
chmod 700 /root/us-live.py

cat > /root/us-live.sh <<'LIVE'
#!/usr/bin/env bash
set -euo pipefail
/usr/bin/python3 /root/us-live.py
LIVE
chmod 700 /root/us-live.sh
/root/us-live.sh || true

echo "== cron =="
(crontab -l 2>/dev/null | grep -v 'us-live\|us-stock-sync' || true
 echo '* * * * * /root/us-live.sh  # us-live'
 echo '*/2 * * * * /root/us-stock-sync.sh >> /root/us-sync.log 2>&1  # us-stock-sync') | crontab -
systemctl enable cron >/dev/null || true
systemctl restart cron || true

echo "== verify =="
nginx -t
printf 'nginx='
systemctl is-active nginx
printf 'cron='
systemctl is-active cron || true
printf 'site_index='
stat -c '%y %n' "$SITE_DIR/index.html"
ls -l "$SITE_DIR/index.html" "$SITE_DIR/data/live.json" 2>/dev/null || true
crontab -l
curl -sI -H "Host: $DOMAIN" http://127.0.0.1/ | sed -n '1,8p'
printf 'live_sample='
if [ -f "$SITE_DIR/data/live.json" ]; then
  head -c 700 "$SITE_DIR/data/live.json"
fi
echo
echo "DEPLOY_OK"
