"""Level-4 memory: notes saved by one run are read into the context of every later run.

This is the part that turns an incident into a condition - a poisoned note keeps firing for
other agents on other tickets after the attacker is gone (OWASP ASI06 Memory Poisoning).
Memory is only wired in at LEVEL >= 4 (see agent.py), both for reading and for the save_note tool.
"""
from __future__ import annotations

from .store import Store


class Memory:
    def __init__(self, store: Store, limit: int = 20):
        self.store = store
        self.limit = limit

    def recall(self) -> list[str]:
        return [f"[{n['author']}] {n['text']}" for n in self.store.list_notes(self.limit)]

    def save(self, author: str, text: str) -> int:
        return self.store.add_note(author, text)

    def clear(self) -> None:
        self.store.clear_notes()
