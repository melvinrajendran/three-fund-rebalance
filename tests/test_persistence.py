import json
from decimal import Decimal

import pytest

from three_fund_rebalance.models import (
    Account,
    FundType,
    Holding,
    TaxTreatment,
    TDFAllocation,
)
from three_fund_rebalance.persistence import (
    PersistedConfig,
    PersistenceError,
    load_config,
    save_config,
)


def sample_config() -> PersistedConfig:
    tdf = TDFAllocation(
        domestic_equity_pct=Decimal(60), international_equity_pct=Decimal(20), bond_pct=Decimal(20)
    )
    account = Account(
        account_type="Roth 401(k)",
        name="Acme 401k",
        tax_treatment=TaxTreatment.TAX_ADVANTAGED,
        holdings=[
            Holding(fund_type=FundType.DOMESTIC_EQUITY, name="VTI", balance=Decimal("6000.00")),
            Holding(fund_type=FundType.TDF, name="Target 2050", balance=Decimal(0), tdf_allocation=tdf),
            Holding(fund_type=FundType.CASH, name="", balance=Decimal("125.50")),
        ],
    )
    return PersistedConfig(
        stock_pct=Decimal(80),
        bond_pct=Decimal(20),
        vt_us_pct=Decimal("61.9"),
        vt_as_of="June 30, 2026",
        balances_as_of="2026-08-18",
        accounts=[account],
    )


class TestRoundTrip:
    def test_save_then_load_reproduces_config(self, tmp_path):
        path = tmp_path / "config.json"
        original = sample_config()
        save_config(path, original)
        loaded = load_config(path)

        assert loaded.stock_pct == original.stock_pct
        assert loaded.bond_pct == original.bond_pct
        assert loaded.vt_us_pct == original.vt_us_pct
        assert loaded.vt_as_of == original.vt_as_of
        assert loaded.balances_as_of == original.balances_as_of
        assert len(loaded.accounts) == 1

        original_account = original.accounts[0]
        loaded_account = loaded.accounts[0]
        assert loaded_account.account_type == original_account.account_type
        assert loaded_account.name == original_account.name
        assert loaded_account.tax_treatment == original_account.tax_treatment
        assert loaded_account.total_value() == original_account.total_value()

        loaded_tdf_holding = loaded_account.get_holding(FundType.TDF)
        assert loaded_tdf_holding.tdf_allocation == tdf_of(original_account)

    def test_load_missing_file_returns_blank_config(self, tmp_path):
        config = load_config(tmp_path / "does_not_exist.json")
        assert config.accounts == []
        assert config.stock_pct is None

    def test_saved_file_is_pretty_printed_json(self, tmp_path):
        path = tmp_path / "config.json"
        save_config(path, sample_config())
        text = path.read_text()
        assert text.endswith("\n")
        parsed = json.loads(text)
        assert parsed["schema_version"] == 1
        assert parsed["accounts"][0]["name"] == "Acme 401k"


def tdf_of(account: Account) -> TDFAllocation:
    return account.get_holding(FundType.TDF).tdf_allocation


class TestErrorHandling:
    def test_corrupt_json_raises_persistence_error(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text("{not valid json")
        with pytest.raises(PersistenceError, match="Could not read"):
            load_config(path)

    def test_non_object_json_raises_persistence_error(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text("[1, 2, 3]")
        with pytest.raises(PersistenceError, match="JSON object"):
            load_config(path)

    def test_unsupported_schema_version_raises(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"schema_version": 999, "accounts": []}))
        with pytest.raises(PersistenceError, match="Unsupported config schema_version"):
            load_config(path)

    def test_invalid_account_data_raises(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "accounts": [{"account_type": "Roth IRA", "name": "X"}],  # missing tax_treatment
                }
            )
        )
        with pytest.raises(PersistenceError, match="Invalid account"):
            load_config(path)

    def test_unparseable_stock_pct_raises(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"schema_version": 1, "stock_pct": "not-a-number", "accounts": []}))
        with pytest.raises(PersistenceError, match="Could not parse 'stock_pct'"):
            load_config(path)

    def test_incomplete_tdf_allocation_raises(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "accounts": [
                        {
                            "account_type": "Roth IRA",
                            "name": "X",
                            "tax_treatment": "tax_advantaged",
                            "holdings": [
                                {
                                    "fund_type": "tdf",
                                    "name": "Target 2050",
                                    "balance": "1000",
                                    # missing "bond_pct"
                                    "tdf_allocation": {
                                        "domestic_equity_pct": "60",
                                        "international_equity_pct": "20",
                                    },
                                }
                            ],
                        }
                    ],
                }
            )
        )
        with pytest.raises(PersistenceError, match="Invalid tdf_allocation"):
            load_config(path)

    def test_holding_failing_model_validation_raises(self, tmp_path):
        # An empty name is valid JSON but rejected by Holding's own validation
        # for a non-cash fund type -- this should surface as PersistenceError,
        # not an uncaught ValueError.
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "accounts": [
                        {
                            "account_type": "Roth IRA",
                            "name": "X",
                            "tax_treatment": "tax_advantaged",
                            "holdings": [
                                {"fund_type": "domestic_equity", "name": "", "balance": "100"}
                            ],
                        }
                    ],
                }
            )
        )
        with pytest.raises(PersistenceError, match="Invalid holding"):
            load_config(path)

    def test_invalid_holding_fund_type_raises(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "accounts": [
                        {
                            "account_type": "Roth IRA",
                            "name": "X",
                            "tax_treatment": "tax_advantaged",
                            "holdings": [{"fund_type": "bogus", "name": "VTI", "balance": "100"}],
                        }
                    ],
                }
            )
        )
        with pytest.raises(PersistenceError, match="Invalid holding"):
            load_config(path)


class TestSaveIsAtomic:
    def test_save_does_not_leave_temp_files_behind(self, tmp_path):
        path = tmp_path / "config.json"
        save_config(path, sample_config())
        leftover_temp_files = list(tmp_path.glob(".*.tmp"))
        assert leftover_temp_files == []

    def test_overwriting_existing_config_succeeds(self, tmp_path):
        path = tmp_path / "config.json"
        save_config(path, sample_config())
        updated = sample_config()
        updated.stock_pct = Decimal(70)
        save_config(path, updated)
        assert load_config(path).stock_pct == Decimal(70)

    def test_temp_file_cleaned_up_if_replace_fails(self, tmp_path, monkeypatch):
        import three_fund_rebalance.persistence as persistence_module

        def raise_os_error(_src, _dst):
            raise OSError("simulated failure")

        monkeypatch.setattr(persistence_module.os, "replace", raise_os_error)
        path = tmp_path / "config.json"
        with pytest.raises(OSError, match="simulated failure"):
            save_config(path, sample_config())
        assert list(tmp_path.glob(".*.tmp")) == []
        assert not path.exists()
