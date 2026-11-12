"""SQLite-backed store for tickets, notes (memory) and the
simulated side effects: emails, refunds and discount codes.

Lives in /tmp on Hugging Face Spaces, so everything vanishes on restart - by design.
Customers and their orders are read-only synthetic data loaded from data/customers.json.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at REAL NOT NULL,
  source TEXT NOT NULL,            -- seed | presenter (added on the page)
  name TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '',
  order_ref TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  poisoned INTEGER NOT NULL DEFAULT 0,
  session TEXT NOT NULL DEFAULT '',
  example_level INTEGER NOT NULL DEFAULT 0   -- 1-4: a one-level example ticket (T6-T9), kept out of the inbox run
);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at REAL NOT NULL,
  author TEXT NOT NULL,
  text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS emails (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at REAL NOT NULL,
  to_addr TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refunds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at REAL NOT NULL,
  order_id TEXT NOT NULL,
  amount REAL NOT NULL,
  reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS discounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at REAL NOT NULL,
  code TEXT NOT NULL,
  customer_email TEXT NOT NULL,
  percent REAL NOT NULL,
  reason TEXT NOT NULL DEFAULT ''
);
"""


class Store:
    def __init__(self, db_path: str = ":memory:"):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(tickets)")}
            if "example_level" not in cols:      # databases created before the level examples existed
                self._conn.execute("ALTER TABLE tickets ADD COLUMN example_level INTEGER NOT NULL DEFAULT 0")
                self._conn.commit()
        self.customers: dict[str, dict[str, Any]] = {}
        self._orders: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}   # order id -> (order, customer)

    # ---- customers and orders (read-only synthetic data) ----
    def load_customers(self, path: str | Path) -> None:
        data = json.loads(Path(path).read_text())
        self.customers = {c["id"]: c for c in data}
        self._orders = {o["id"].upper(): (o, c) for c in data for o in c.get("orders", [])}

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        """The order with its customer's contact details, as a support lookup would show it."""
        hit = self._orders.get(str(order_id).strip().upper())
        if not hit:
            return None
        order, cust = hit
        contact = {k: cust[k] for k in ("id", "name", "email", "phone", "shipping_address", "loyalty_tier")}
        return {**order, "refunded_so_far": self.refunded_total(order["id"]), "customer": contact,
                "note": cust.get("note", "")}

    def customer_emails(self) -> set[str]:
        return {c["email"].lower() for c in self.customers.values()}

    def orders_for_email(self, email: str) -> set[str]:
        email = (email or "").strip().lower()
        return {o["id"].upper() for c in self.customers.values() if c["email"].lower() == email
                for o in c.get("orders", [])}

    # ---- tickets ----
    def add_ticket(self, name: str, email: str, order_ref: str, message: str, *,
                   source: str, poisoned: bool = False, session: str = "", example_level: int = 0) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tickets (created_at, source, name, email, order_ref, message, poisoned, session, example_level)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (time.time(), source, name, email, order_ref, message, int(poisoned), session, int(example_level)),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def seed_tickets(self, path: str | Path, exfil_address: str) -> None:
        """Load the seed inbox. `{EXFIL_ADDRESS}` in messages is substituted.
        On restart, existing seed rows are refreshed in place so edits to the JSON take effect
        without changing ticket IDs."""
        for t in json.loads(Path(path).read_text()):
            message = t["message"].replace("{EXFIL_ADDRESS}", exfil_address)
            level = int(t.get("example_level", 0))
            with self._lock:
                cur = self._conn.execute(
                    "UPDATE tickets SET email=?, order_ref=?, message=?, poisoned=?, example_level=? WHERE source='seed' AND name=?",
                    (t.get("email", ""), t.get("order_ref", ""), message, int(bool(t.get("poisoned"))), level, t["name"]))
                self._conn.commit()
            if cur.rowcount == 0:
                self.add_ticket(t["name"], t.get("email", ""), t.get("order_ref", ""), message,
                                source="seed", poisoned=bool(t.get("poisoned")), example_level=level)

    def load_extra_tickets(self, path: str | Path) -> int:
        """Add tickets from a JSON file to the inbox, skipping names already present."""
        existing = {t["name"] for t in self.list_tickets()}
        added = 0
        for t in json.loads(Path(path).read_text()):
            if t["name"] in existing:
                continue
            self.add_ticket(t["name"], t.get("email", ""), t.get("order_ref", ""), t["message"], source="presenter")
            added += 1
        return added

    def list_tickets(self, sources: tuple[str, ...] = ("seed", "presenter")) -> list[dict]:
        q = f"SELECT * FROM tickets WHERE source IN ({','.join('?'*len(sources))}) ORDER BY id"
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, sources).fetchall()]

    def inbox_tickets(self) -> list[dict]:
        """Today's inbox: every ticket except the one-level examples. This is what a full run summarises."""
        return [t for t in self.list_tickets() if not t["example_level"]]

    def example_tickets(self) -> list[dict]:
        """The one-level examples (T6-T9), each meant to be run on its own at its level."""
        return [t for t in self.list_tickets() if t["example_level"]]

    def get_ticket(self, ticket_id: int) -> dict | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        return dict(r) if r else None

    # ---- notes (level-4 memory) ----
    def add_note(self, author: str, text: str) -> int:
        with self._lock:
            cur = self._conn.execute("INSERT INTO notes (created_at, author, text) VALUES (?,?,?)",
                                     (time.time(), author, text))
            self._conn.commit()
            return int(cur.lastrowid)

    def list_notes(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()][::-1]

    def clear_notes(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM notes")
            self._conn.commit()

    # ---- simulated side effects: outbox, refunds, discount codes ----
    def log_email(self, to_addr: str, subject: str, body: str) -> int:
        with self._lock:
            cur = self._conn.execute("INSERT INTO emails (created_at, to_addr, subject, body) VALUES (?,?,?,?)",
                                     (time.time(), to_addr, subject, body))
            self._conn.commit()
            return int(cur.lastrowid)

    def list_emails(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT * FROM emails ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def log_refund(self, order_id: str, amount: float, reason: str) -> int:
        with self._lock:
            cur = self._conn.execute("INSERT INTO refunds (created_at, order_id, amount, reason) VALUES (?,?,?,?)",
                                     (time.time(), order_id.upper(), amount, reason))
            self._conn.commit()
            return int(cur.lastrowid)

    def refunded_total(self, order_id: str) -> float:
        with self._lock:
            return float(self._conn.execute("SELECT COALESCE(SUM(amount),0) FROM refunds WHERE order_id=?",
                                            (str(order_id).upper(),)).fetchone()[0])

    def list_refunds(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT * FROM refunds ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def create_discount(self, customer_email: str, percent: float, reason: str) -> str:
        code = f"LG-{int(percent)}-{secrets.token_hex(2).upper()}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO discounts (created_at, code, customer_email, percent, reason) VALUES (?,?,?,?,?)",
                (time.time(), code, customer_email, percent, reason))
            self._conn.commit()
        return code

    def list_discounts(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT * FROM discounts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def reset_demo(self) -> None:
        """Presenter 'reset' button: clears notes, emails, refunds, discounts, non-seed tickets and statuses."""
        with self._lock:
            self._conn.executescript(
                "DELETE FROM notes; DELETE FROM emails; DELETE FROM refunds; DELETE FROM discounts;"
                "DELETE FROM tickets WHERE source!='seed'; UPDATE tickets SET status='open';")
            self._conn.commit()
