import sqlite3


class ManagedConnection(sqlite3.Connection):
    """sqlite3's normal context manager commits but does not close the connection."""
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()
