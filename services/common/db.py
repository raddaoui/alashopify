"""Tiny MySQL helper shared by the data services."""
import os

import pymysql


def get_connection() -> "pymysql.connections.Connection":
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "mysql"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "shop"),
        password=os.getenv("MYSQL_PASSWORD", "shoppass"),
        database=os.getenv("MYSQL_DATABASE", "shopdb"),
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
        read_timeout=10,
        write_timeout=10,
    )


def ping() -> None:
    conn = get_connection()
    try:
        conn.ping(reconnect=False)
    finally:
        conn.close()
