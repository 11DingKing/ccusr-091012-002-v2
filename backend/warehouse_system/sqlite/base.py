"""
自定义 SQLite 后端。

两项加固，使多人并发签署在 SQLite 上具有确定结果：

1. WAL 日志模式：读者不再阻塞写者提交，消除默认回滚日志模式下
   “写者等待读者释放 SHARED 锁、读者等待写者提交”的死锁
   （OperationalError: database table is locked）；
2. 事务以 BEGIN IMMEDIATE 启动：进入事务即获取 RESERVED 写锁，
   并发写事务在数据库层串行化，后到事务按前者已提交的数据重新求值。

配合应用层的条件更新（CAS）与锁冲突重试（见 approval_engine._with_lock_retry），
同一签署行在并发下只会被推进一次。PostgreSQL 等后端本身具备行锁，无需这些设置。
"""
from django.db.backends.sqlite3.base import (  # noqa: F401
    DatabaseWrapper as SQLiteDatabaseWrapper,
)


class DatabaseWrapper(SQLiteDatabaseWrapper):
    def get_new_connection(self, conn_params):
        conn = super().get_new_connection(conn_params)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _start_transaction_under_autocommit(self):
        self.cursor().execute("BEGIN IMMEDIATE")
