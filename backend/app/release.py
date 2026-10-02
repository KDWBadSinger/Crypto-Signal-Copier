VERSION = '0.4.7-local'
from contextlib import closing


def backup_before_upgrade(data_dir):
    import sqlite3
    source = data_dir / 'mia_copier.sqlite3'
    target = data_dir / 'backups' / f'pre-{VERSION}.sqlite3'
    if source.exists() and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.partial')
        with closing(sqlite3.connect(source)) as reader, closing(sqlite3.connect(temporary)) as writer:
            reader.backup(writer)
            if writer.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('升级前数据库备份校验失败，停止升级')
        temporary.replace(target)


def database_export(path):
    import sqlite3
    with closing(sqlite3.connect(path)) as reader, closing(sqlite3.connect(':memory:')) as writer:
        reader.backup(writer)
        return writer.serialize()
