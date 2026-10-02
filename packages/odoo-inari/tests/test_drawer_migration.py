import importlib.util
from pathlib import Path
import sqlite3


def test_historical_cashbox_projection_keeps_its_identity_and_binding():
    path = Path(__file__).parents[1] / 'inari_devices/migrations/19.0.1.1.0/pre-migrate.py'
    spec = importlib.util.spec_from_file_location('drawer_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with sqlite3.connect(':memory:') as connection:
        connection.execute('CREATE TABLE inari_device_capability (id INTEGER PRIMARY KEY, operation TEXT)')
        connection.execute('CREATE TABLE binding (capability_id INTEGER REFERENCES inari_device_capability(id))')
        connection.execute("INSERT INTO inari_device_capability VALUES (17, 'open_cashbox'), (18, 'receipt_image')")
        connection.execute('INSERT INTO binding VALUES (17)')
        migration.migrate(connection, '19.0.1.0.0')
        migration.migrate(connection, '19.0.1.0.0')
        assert connection.execute('SELECT * FROM inari_device_capability ORDER BY id').fetchall() == [(17, 'open_cash_drawer'), (18, 'receipt_image')]
        assert connection.execute('SELECT capability_id FROM binding').fetchone() == (17,)
