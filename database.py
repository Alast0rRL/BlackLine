"""
BlackLine Database Module
SQLite database for storing connected clients.
"""

import aiosqlite
from typing import Optional
from dataclasses import dataclass
from enum import Enum


class ClientStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"


@dataclass
class Client:
    id: int
    name: str
    status: ClientStatus
    last_seen: Optional[float] = None


DB_PATH = "blackline.db"


async def init_db():
    """Initialize database and create tables."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                status TEXT DEFAULT 'offline',
                last_seen REAL
            )
        """)
        await db.commit()


async def get_or_create_client(name: str) -> Client:
    """Get existing client or create new one."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Try to get existing client
        async with db.execute(
            "SELECT id, name, status, last_seen FROM clients WHERE name = ?",
            (name,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return Client(
                    id=row[0],
                    name=row[1],
                    status=ClientStatus(row[2]),
                    last_seen=row[3]
                )
        
        # Create new client
        import time
        cursor = await db.execute(
            "INSERT INTO clients (name, status, last_seen) VALUES (?, ?, ?)",
            (name, ClientStatus.OFFLINE.value, time.time())
        )
        client_id = cursor.lastrowid
        await db.commit()
        
        return Client(
            id=client_id,
            name=name,
            status=ClientStatus.OFFLINE,
            last_seen=time.time()
        )


async def update_client_status(client_id: int, status: ClientStatus):
    """Update client status and last_seen timestamp."""
    import time
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE clients SET status = ?, last_seen = ? WHERE id = ?",
            (status.value, time.time(), client_id)
        )
        await db.commit()


async def get_all_clients() -> list[Client]:
    """Get all clients from database."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, name, status, last_seen FROM clients"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                Client(
                    id=row[0],
                    name=row[1],
                    status=ClientStatus(row[2]),
                    last_seen=row[3]
                )
                for row in rows
            ]


async def get_client_by_id(client_id: int) -> Optional[Client]:
    """Get client by ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, name, status, last_seen FROM clients WHERE id = ?",
            (client_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return Client(
                    id=row[0],
                    name=row[1],
                    status=ClientStatus(row[2]),
                    last_seen=row[3]
                )
            return None


async def get_client_by_name(name: str) -> Optional[Client]:
    """Get client by name."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, name, status, last_seen FROM clients WHERE name = ?",
            (name,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return Client(
                    id=row[0],
                    name=row[1],
                    status=ClientStatus(row[2]),
                    last_seen=row[3]
                )
            return None


async def delete_client(client_id: int):
    """Delete client from database."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM clients WHERE id = ?", (client_id,))
        await db.commit()
