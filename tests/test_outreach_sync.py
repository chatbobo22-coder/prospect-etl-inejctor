from types import SimpleNamespace

from cnpj_etl.outreach_sync import sync_qualified_leads


class SyncConnection:
    def __init__(self):
        self.query = ""
        self.committed = False

    def execute(self, query, params=None):
        self.query = query
        return SimpleNamespace(rowcount=7)

    def commit(self):
        self.committed = True


def test_sync_only_publishes_qualified_ab_leads():
    conn = SyncConnection()

    synced = sync_qualified_leads(conn)

    assert synced == 7
    assert conn.committed
    assert "p.qualification_status = 'qualified'" in conn.query
    assert "p.lead_quality IN ('A', 'B')" in conn.query
    assert "INSERT INTO outreach.leads" in conn.query
    assert "ON CONFLICT (cnpj) DO UPDATE" in conn.query
    assert "p.whatsapp_url" in conn.query
    assert "whatsapp = EXCLUDED.whatsapp" in conn.query
    assert "'marketing_ready', true" in conn.query
    assert "IS DISTINCT FROM" in conn.query


def test_sync_can_share_an_outer_transaction():
    conn = SyncConnection()

    sync_qualified_leads(conn, commit=False)

    assert not conn.committed
