"""Acceptance coverage for profile360 personal compensation history."""

from app import db as db_module


def test_compensation_model_represents_paye_contract_and_provenance(client):
    with db_module.db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO profile360.episodes
                (episode_key, episode_type, organisation, title, start_date, status)
            VALUES ('salary-history-test-episode', 'employment', 'Example Co', 'Actuary', '2020-01-01', 'active')
            RETURNING id
            """
        )
        episode_id = cur.fetchone()["id"]

        document_ids = []
        for source_key, title, source_type in (
            ('salary-history-test-payslip-1', 'January payslip', 'payslip'),
            ('salary-history-test-payslip-2', 'February payslip', 'payslip'),
            ('salary-history-test-contract', 'Contract', 'contract'),
        ):
            cur.execute(
                """
                INSERT INTO profile360.documents (source_key, title, source_type)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (source_key, title, source_type),
            )
            document_ids.append(cur.fetchone()["id"])

        rows = [
            ('paye-base-1', 'payslip', 'paye', 'annual_base', '2020-01-01', '2021-06-30', None, 80000, 'EUR', 'annual', None),
            ('paye-base-2', 'payslip', 'paye', 'annual_base', '2021-07-01', '2022-12-31', None, 90000, 'EUR', 'annual', None),
            ('paye-bonus', 'payslip', 'paye', 'bonus', '2021-12-01', '2021-12-31', '2021-12-31', 5000, 'EUR', 'one_off', None),
            ('contract-rate-jan', 'contract', 'contract', 'day_rate', '2023-01-01', '2023-01-31', None, 650, 'EUR', 'daily', 20),
            ('contract-gross-jan', 'invoice', 'contract', 'gross_pay', '2023-01-01', '2023-01-31', None, 13000, 'EUR', 'period', None),
            ('contract-rate-feb', 'contract', 'contract', 'day_rate', '2023-02-01', '2023-02-28', None, 650, 'EUR', 'daily', 18),
            ('contract-gross-feb', 'invoice', 'contract', 'gross_pay', '2023-02-01', '2023-02-28', None, 11700, 'EUR', 'period', None),
            ('contract-rate-rise', 'contract', 'contract', 'day_rate', '2023-03-01', None, None, 700, 'EUR', 'daily', None),
        ]

        observation_ids = {}
        for row in rows:
            cur.execute(
                """
                INSERT INTO profile360.compensation_observation (
                    source_key, episode_id, source_kind, employment_basis, component,
                    period_start, period_end, pay_date, amount, currency, unit, quantity,
                    review_status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'accepted')
                RETURNING id
                """,
                (row[0], episode_id, *row[1:]),
            )
            observation_ids[row[0]] = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO profile360.compensation_observation (
                source_key, episode_id, source_kind, employment_basis, component,
                amount, unit, uncertainty, review_status
            )
            VALUES (
                'ambiguous-user-asserted', %s, 'user_asserted', 'paye', 'annual_base',
                100000, 'annual', 'Currency and effective dates not supplied', 'unreviewed'
            )
            """,
            (episode_id,),
        )

        for document_id in document_ids[:2]:
            cur.execute(
                """
                INSERT INTO profile360.compensation_evidence (observation_id, document_id)
                VALUES (%s, %s)
                """,
                (observation_ids['paye-base-1'], document_id),
            )

        cur.execute(
            """
            SELECT component, amount, quantity, period_start, period_end
            FROM profile360.compensation_observation
            WHERE episode_id = %s AND employment_basis = 'contract'
            ORDER BY period_start, component
            """,
            (episode_id,),
        )
        contract_rows = cur.fetchall()
        assert len(contract_rows) == 5
        assert {r["amount"] for r in contract_rows if r["component"] == "gross_pay"} == {13000, 11700}
        assert {r["quantity"] for r in contract_rows if r["component"] == "day_rate" and r["quantity"] is not None} == {20, 18}
        assert max(r["amount"] for r in contract_rows if r["component"] == "day_rate") == 700

        cur.execute(
            """
            SELECT count(*) AS n
            FROM profile360.compensation_evidence
            WHERE observation_id = %s
            """,
            (observation_ids['paye-base-1'],),
        )
        assert cur.fetchone()["n"] == 2

        cur.execute(
            """
            SELECT currency, period_start, period_end, uncertainty
            FROM profile360.compensation_observation
            WHERE source_key = 'ambiguous-user-asserted'
            """
        )
        ambiguous = cur.fetchone()
        assert ambiguous["currency"] is None
        assert ambiguous["period_start"] is None
        assert ambiguous["period_end"] is None
        assert ambiguous["uncertainty"]
