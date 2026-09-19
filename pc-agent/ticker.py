"""Ticker channel for the EchoPortal agent: live crypto + stock prices for the Spot.

Config: tickers.json next to this file (edited from the tray's "Tickers..." dialog):
  {"crypto": ["BTC", "ETH"], "stocks": ["NVDA", "AAPL"], "currency": "USD",
   "stock_interval": 10, "cycle_seconds": 8}

Crypto: Coinbase Exchange WebSocket ticker feed (real-time, every trade), 24h change from open_24h.
        Sparklines from CoinGecko market_chart every 5 min. Non-Coinbase pairs fall back to CoinGecko polling.
Stocks: Yahoo Finance v8 chart, polled every stock_interval seconds (price/change), sparkline every 5 min.

Broadcast ({"ch":"ticker","data":{...}}), throttled to ~4/s:
  items: [{sym, name, kind, price, change_pct, currency, spark: [..], market, live: bool}]
  updated: unix seconds, cycle: seconds (0 = off), error: str|None
"""
import asyncio
import copy
import json
import os
import time

import aiohttp

import config as agent_config

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = agent_config.data_path("tickers.json")
DEFAULT = {"crypto": ["BTC", "ETH"], "stocks": ["NVDA", "AAPL", "MSFT"], "currency": "USD",
           "stock_interval": 10, "cycle_seconds": 8, "interval": 60}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EchoPortalAgent/1.0"}
COINBASE_WS = "wss://ws-feed.exchange.coinbase.com"

COIN_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple", "DOGE": "dogecoin", "ADA": "cardano",
            "BNB": "binancecoin", "LTC": "litecoin", "DOT": "polkadot", "AVAX": "avalanche-2", "LINK": "chainlink",
            "MATIC": "matic-network", "SHIB": "shiba-inu", "TRX": "tron", "USDT": "tether", "USDC": "usd-coin"}
COIN_NAMES = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "XRP": "XRP", "DOGE": "Dogecoin", "ADA": "Cardano",
              "LTC": "Litecoin", "LINK": "Chainlink", "AVAX": "Avalanche", "DOT": "Polkadot"}


def load_config() -> dict:
    try:
        with open(CONFIG, encoding="utf-8") as f:
            c = json.load(f)
        return {**copy.deepcopy(DEFAULT), **c}
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(DEFAULT)


def save_config(c: dict):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=2)


def _cfg_mtime():
    try:
        return os.path.getmtime(CONFIG)
    except OSError:
        return 0.0


class TickerWatcher:
    def __init__(self, broadcast):
        self.broadcast = broadcast
        self.cfg = load_config()
        self.items = {}            # sym -> item dict (insertion order = display order)
        self.error = None
        self._sparks = {}          # sym -> (fetched_at, [floats])
        self._coin_id_cache = dict(COIN_IDS)
        self._dirty = asyncio.Event() if False else None
        self._last_sent = 0.0
        self._pending = False
        self._generation = 0       # bumps when the config changes; workers exit and restart

    # ------------------------------------------------------------------ public
    def snapshot(self) -> dict:
        return {"items": list(self.items.values()), "updated": int(time.time()),
                "cycle": int(self.cfg.get("cycle_seconds", 0) or 0), "error": self.error}

    async def run(self):
        mtime = _cfg_mtime()
        while True:
            self.cfg = load_config()
            self._generation += 1
            gen = self._generation
            self.items = {}
            order = [c.strip().upper() for c in self.cfg.get("crypto", []) if c.strip()] + \
                    [t.strip().upper() for t in self.cfg.get("stocks", []) if t.strip()]
            cur = (self.cfg.get("currency") or "USD").upper()
            for sym in order:
                kind = "crypto" if sym in [c.strip().upper() for c in self.cfg.get("crypto", [])] else "stock"
                self.items[sym] = {"sym": sym, "name": COIN_NAMES.get(sym, sym), "kind": kind, "price": None,
                                   "change_pct": None, "currency": cur if kind == "crypto" else "USD",
                                   "spark": [], "market": "24h" if kind == "crypto" else "", "live": False}
            tasks = [
                asyncio.create_task(self._pusher(gen)),
                asyncio.create_task(self._crypto_stream(gen, cur)),
                asyncio.create_task(self._crypto_sparks(gen, cur)),
                asyncio.create_task(self._stocks(gen)),
            ]
            # wait for a config change
            while _cfg_mtime() == mtime:
                await asyncio.sleep(1)
            mtime = _cfg_mtime()
            print("ticker: config changed, restarting workers")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------ broadcast throttle
    def _mark(self):
        self._pending = True

    async def _pusher(self, gen):
        while gen == self._generation:
            await asyncio.sleep(0.25)
            if self._pending:
                self._pending = False
                self._last_sent = time.time()
                await self.broadcast(self.snapshot())

    # ------------------------------------------------------------------ crypto: Coinbase stream
    async def _fx_rate(self, gen, cur):
        """USD -> cur rate, refreshed every 10 min (Coinbase streams USD pairs; we convert for CAD/EUR/...)."""
        while gen == self._generation:
            try:
                async with aiohttp.ClientSession(headers=UA) as s:
                    async with s.get("https://api.coinbase.com/v2/exchange-rates", params={"currency": "USD"},
                                     timeout=aiohttp.ClientTimeout(total=10)) as r:
                        d = await r.json()
                rate = float(d["data"]["rates"][cur])
                if rate != self._fx:
                    self._fx = rate
                    print("ticker: USD->%s = %.4f" % (cur, rate))
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001
                print("ticker: fx rate failed:", e)
            await asyncio.sleep(600)

    async def _crypto_stream(self, gen, cur):
        syms = [s for s, it in self.items.items() if it["kind"] == "crypto"]
        if not syms:
            return
        # Coinbase's deep books are the USD pairs; stream those and convert with a live FX rate
        products = ["%s-USD" % s for s in syms]
        self._fx = 1.0
        if cur != "USD":
            asyncio.create_task(self._fx_rate(gen, cur))
        backoff = 2
        while gen == self._generation:
            try:
                async with aiohttp.ClientSession(headers=UA) as s, s.ws_connect(COINBASE_WS, heartbeat=20) as ws:
                    await ws.send_json({"type": "subscribe", "product_ids": products, "channels": ["ticker"]})
                    backoff = 2
                    async for msg in ws:
                        if gen != self._generation:
                            return
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        m = json.loads(msg.data)
                        t = m.get("type")
                        if t == "ticker":
                            sym = m.get("product_id", "").split("-")[0]
                            it = self.items.get(sym)
                            if not it:
                                continue
                            try:
                                price = float(m["price"]); open24 = float(m.get("open_24h") or 0)
                            except (KeyError, ValueError):
                                continue
                            it["price"] = round(price * self._fx, 2 if price * self._fx >= 1 else 6)
                            if open24:
                                it["change_pct"] = round((price - open24) / open24 * 100, 2)
                            it["live"] = True
                            self._mark()
                        elif t == "subscriptions":
                            got = {c.get("name"): c.get("product_ids", []) for c in m.get("channels", [])}
                            print("ticker: coinbase live for", got.get("ticker", []))
                            missing = [p for p in products if p not in got.get("ticker", [])]
                            if missing:
                                print("ticker: no coinbase feed for", missing, "-> CoinGecko polling")
                                asyncio.create_task(self._crypto_poll(gen, [p.split("-")[0] for p in missing], cur))
                        elif t == "error":
                            print("ticker: coinbase error:", m.get("message"), m.get("reason"))
                            # e.g. an unknown product: fall back to polling for everything
                            asyncio.create_task(self._crypto_poll(gen, syms, cur))
                            return
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001
                print("ticker: coinbase stream lost:", repr(e)[:80])
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _coin_id(self, s, sym):
        if sym in self._coin_id_cache:
            return self._coin_id_cache[sym]
        try:
            async with s.get("https://api.coingecko.com/api/v3/search", params={"query": sym}, timeout=aiohttp.ClientTimeout(total=10)) as r:
                d = await r.json()
            for c in d.get("coins", []):
                if c.get("symbol", "").upper() == sym:
                    self._coin_id_cache[sym] = c["id"]
                    COIN_NAMES.setdefault(sym, c.get("name", sym))
                    return c["id"]
        except Exception:  # noqa: BLE001
            pass
        return None

    async def _crypto_poll(self, gen, syms, cur):
        """Fallback for coins Coinbase does not carry in this currency: CoinGecko every 30 s."""
        vs = cur.lower()
        while gen == self._generation:
            try:
                async with aiohttp.ClientSession(headers=UA) as s:
                    ids = {sym: await self._coin_id(s, sym) for sym in syms}
                    ids = {k: v for k, v in ids.items() if v}
                    if ids:
                        async with s.get("https://api.coingecko.com/api/v3/simple/price",
                                         params={"ids": ",".join(ids.values()), "vs_currencies": vs, "include_24hr_change": "true"},
                                         timeout=aiohttp.ClientTimeout(total=10)) as r:
                            d = await r.json()
                        for sym, cid in ids.items():
                            p = d.get(cid, {})
                            it = self.items.get(sym)
                            if it and p.get(vs) is not None:
                                it["price"] = p.get(vs); it["change_pct"] = round(p.get(vs + "_24h_change") or 0, 2)
                                it["name"] = COIN_NAMES.get(sym, sym)
                                self._mark()
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001
                print("ticker: coingecko poll failed:", e)
            await asyncio.sleep(30)

    async def _crypto_sparks(self, gen, cur):
        vs = cur.lower()
        while gen == self._generation:
            try:
                async with aiohttp.ClientSession(headers=UA) as s:
                    for sym, it in list(self.items.items()):
                        if it["kind"] != "crypto":
                            continue
                        cid = await self._coin_id(s, sym)
                        if not cid:
                            continue
                        it["name"] = COIN_NAMES.get(sym, sym)
                        async with s.get("https://api.coingecko.com/api/v3/coins/%s/market_chart" % cid,
                                         params={"vs_currency": vs, "days": "1"}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                            d = await r.json()
                        pts = [p[1] for p in d.get("prices", [])]
                        step = max(1, len(pts) // 60)
                        it["spark"] = [round(v, 4) for v in pts[::step]][-60:]
                        self._mark()
                        await asyncio.sleep(2)   # be polite to the free tier
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001
                print("ticker: sparkline fetch failed:", e)
            await asyncio.sleep(300)

    # ------------------------------------------------------------------ stocks: Yahoo polling
    async def _stocks(self, gen):
        syms = [s for s, it in self.items.items() if it["kind"] == "stock"]
        if not syms:
            return
        interval = max(3, int(self.cfg.get("stock_interval", 10) or 10))
        last_spark = {}
        while gen == self._generation:
            try:
                async with aiohttp.ClientSession(headers=UA) as s:
                    for sym in syms:
                        it = self.items[sym]
                        want_spark = time.time() - last_spark.get(sym, 0) > 300
                        async with s.get("https://query1.finance.yahoo.com/v8/finance/chart/%s" % sym,
                                         params={"range": "1d", "interval": "5m", "includePrePost": "false"},
                                         timeout=aiohttp.ClientTimeout(total=10)) as r:
                            d = await r.json()
                        res = d["chart"]["result"][0]
                        m = res["meta"]
                        price = m.get("regularMarketPrice")
                        prev = m.get("chartPreviousClose") or m.get("previousClose")
                        it["price"] = price
                        it["change_pct"] = round((price - prev) / prev * 100, 2) if price and prev else None
                        it["currency"] = m.get("currency", "USD")
                        it["name"] = m.get("shortName") or m.get("longName") or sym
                        state = (m.get("marketState") or "").upper()
                        it["market"] = "open" if state == "REGULAR" else ("pre" if state == "PRE" else ("post" if state.startswith("POST") else "closed"))
                        it["live"] = state == "REGULAR"
                        if want_spark:
                            closes = [c for c in (res.get("indicators", {}).get("quote", [{}])[0].get("close") or []) if c is not None]
                            step = max(1, len(closes) // 60)
                            it["spark"] = [round(v, 4) for v in closes[::step]][-60:]
                            last_spark[sym] = time.time()
                        self._mark()
                        await asyncio.sleep(0.3)
                self.error = None
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001
                self.error = "stocks: %s" % str(e)[:50]
                print("ticker: stock poll failed:", e)
            await asyncio.sleep(interval)
