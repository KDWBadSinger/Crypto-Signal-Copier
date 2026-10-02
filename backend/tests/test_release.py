import sqlite3
from app.release import VERSION, backup_before_upgrade, database_export


def test_upgrade_backup_and_export_are_valid_and_do_not_replace_first_backup(tmp_path):
    path=tmp_path/'mia_copier.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE example(value TEXT)')
        conn.execute('INSERT INTO example VALUES (?)',('original',))
    backup_before_upgrade(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute('INSERT INTO example VALUES (?)',('later',))
    backup_before_upgrade(tmp_path)
    with sqlite3.connect(tmp_path/'backups'/f'pre-{VERSION}.sqlite3') as conn:
        assert conn.execute('SELECT COUNT(*) FROM example').fetchone()[0]==1
    with sqlite3.connect(':memory:') as conn:
        conn.deserialize(database_export(path))
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert conn.execute('SELECT COUNT(*) FROM example').fetchone()[0]==2
