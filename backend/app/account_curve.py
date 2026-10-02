"""Observed real-account equity; never label balance changes as copier profit."""
import hashlib


class AccountCurve:
    def __init__(self, store):
        self.store = store
        with store._connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS account_equity_observations (scope TEXT, day TEXT, observed_at TEXT, equity TEXT, PRIMARY KEY(scope, day))')

    @staticmethod
    def scope(settings):
        return hashlib.sha256((settings.bitget_api_environment+'|'+(settings.bitget_api_key or '')).encode()).hexdigest()

    def record(self, settings, snapshot):
        if settings.bitget_is_demo or not settings.bitget_configured or snapshot.environment != 'live':
            return
        equity = snapshot.account_equity_usdt
        if not equity.is_finite() or equity < 0:
            return
        timestamp = snapshot.updated_at.isoformat()
        with self.store._connect() as conn:
            conn.execute('INSERT INTO account_equity_observations VALUES (?,?,?,?) ON CONFLICT(scope,day) DO UPDATE SET observed_at=excluded.observed_at,equity=excluded.equity WHERE excluded.observed_at > account_equity_observations.observed_at',
                         (self.scope(settings),timestamp[:10],timestamp,str(equity)))

    def report(self, settings):
        with self.store._connect() as conn:
            rows = conn.execute('SELECT day, observed_at, equity FROM account_equity_observations WHERE scope=? ORDER BY day', (self.scope(settings),)).fetchall() if settings.bitget_configured and not settings.bitget_is_demo else []
        return {'environment': settings.bitget_api_environment, 'equity_points': [dict(r) for r in rows],
                'follow_points': [], 'follow_available': False,
                'detail': '当前版本禁止实盘开单，尚无可归因到本程序的真实跟单成交收益。账户权益不是跟单收益；需关联开平仓成交、手续费和资金费率后才能生成跟单净收益曲线。'}
