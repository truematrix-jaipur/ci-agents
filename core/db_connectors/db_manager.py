import mysql.connector
from mysql.connector import Error
import redis
import chromadb
import logging
import sys
import os

# Append project root to path for absolute imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from config.settings import config

logger = logging.getLogger(__name__)

class DBManager:
    def __init__(self):
        self._mysql_pool = None
        self._erpnext_mysql_pool = None
        self._redis_client = None
        self._erpnext_redis_client = None
        self._chroma_client = None

    def _init_mysql_pool(self):
        if not self._mysql_pool:
            try:
                from mysql.connector import pooling
                self._mysql_pool = pooling.MySQLConnectionPool(
                    pool_name="agent_pool",
                    pool_size=5,
                    host=config.MYSQL_HOST,
                    port=config.MYSQL_PORT,
                    user=config.MYSQL_USER,
                    password=config.MYSQL_PASSWORD,
                    database=config.MYSQL_DATABASE,
                    charset='utf8mb4',
                    collation='utf8mb4_general_ci'
                )
            except Error as e:
                logger.error(f"Failed to init MySQL pool: {e}")

    def _init_erpnext_pool(self):
        if not self._erpnext_mysql_pool:
            try:
                from mysql.connector import pooling
                self._erpnext_mysql_pool = pooling.MySQLConnectionPool(
                    pool_name="erpnext_pool",
                    pool_size=5,
                    host=config.ERPNEXT_MYSQL_HOST,
                    port=config.ERPNEXT_MYSQL_PORT,
                    user=config.ERPNEXT_MYSQL_USER,
                    password=config.ERPNEXT_MYSQL_PASSWORD,
                    database=config.ERPNEXT_MYSQL_DATABASE,
                    charset='utf8mb4',
                    collation='utf8mb4_general_ci'
                )
            except Error as e:
                logger.error(f"Failed to init ERPNext pool: {e}")

    def get_mysql_connection(self):
        self._init_mysql_pool()
        if self._mysql_pool is None:
            logger.error("MySQL pool is not initialized")
            return None
        try:
            return self._mysql_pool.get_connection()
        except Error as e:
            logger.error(f"Error getting MySQL connection: {e}")
            return None

    def get_erpnext_mysql_connection(self):
        self._init_erpnext_pool()
        if self._erpnext_mysql_pool is None:
            logger.error("ERPNext MySQL pool is not initialized")
            return None
        try:
            return self._erpnext_mysql_pool.get_connection()
        except Error as e:
            logger.error(f"Error getting ERPNext connection: {e}")
            return None

    def get_redis_client(self):
        if not self._redis_client:
            self._redis_client = redis.Redis(
                host=config.REDIS_HOST,
                port=config.REDIS_PORT,
                db=config.REDIS_DB,
                decode_responses=True
            )
        return self._redis_client

    def get_erpnext_redis_client(self):
        if not self._erpnext_redis_client:
            self._erpnext_redis_client = redis.Redis(
                host=config.ERPNEXT_REDIS_HOST,
                port=config.ERPNEXT_REDIS_PORT,
                db=config.ERPNEXT_REDIS_DB,
                decode_responses=True
            )
        return self._erpnext_redis_client

    def get_chroma_client(self):
        if not self._chroma_client:
            self._chroma_client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
        return self._chroma_client

db_manager = DBManager()
