import json
from decimal import Decimal

import pytest

from three_fund_rebalance.config import SCHEMA_VERSION
from three_fund_rebalance.models import (
    Account,
    FundType,
    Holding,
    TargetDateAllocation,
    TaxTreatment,
)
from three_fund_rebalance.persistence import (
    PersistedConfig,
    PersistenceError,
    _upgrade_v1,
    _upgrade_v2,
    config_from_dict,
    load_config,
    save_config,
)


def sample_config() -> PersistedConfig:
    allocation = TargetDateAllocation(
        us_stock_pct=Decimal(60), international_stock_pct=Decimal(20), bond_pct=Decimal(20)
    )
    target_date_account = Account(
        account_type="Roth 401(k)",
        name="Acme 401k",
        tax_treatment=TaxTreatment.TAX_DEFERRED,
        holdings=[
            Holding(
                fund_type=FundType.TARGET_DATE,
                name="Target 2050",
                value=Decimal(0),
                target_date_allocation=allocation,
            ),
            Holding(fund_type=FundType.CASH, name="", value=Decimal("125.50")),
        ],
    )
    individual_fund_account = Account(
        account_type="Brokerage",
        name="Brokerage",
        tax_treatment=TaxTreatment.TAXABLE,
        holdings=[
            Holding(fund_type=FundType.US_STOCK, name="VTI", value=Decimal("6000.00")),
            Holding(fund_type=FundType.INTERNATIONAL_STOCK, name="VXUS", value=Decimal("2000.00")),
        ],
    )
    return PersistedConfig(
        stock_pct=Decimal(80),
        bond_pct=Decimal(20),
        vt_us_pct=Decimal("61.9"),
        vt_as_of="June 30, 2026",
        values_as_of="2026-08-18",
        accounts=[target_date_account, individual_fund_account],
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
        assert loaded.values_as_of == original.values_as_of
        assert len(loaded.accounts) == 2

        original_account = original.accounts[0]
        loaded_account = loaded.accounts[0]
        assert loaded_account.account_type == original_account.account_type
        assert loaded_account.name == original_account.name
        assert loaded_account.tax_treatment == original_account.tax_treatment
        assert loaded_account.total_value() == original_account.total_value()

        loaded_target_date_holding = loaded_account.get_holding(FundType.TARGET_DATE)
        assert loaded_target_date_holding.target_date_allocation == target_date_allocation_of(
            original_account
        )

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
        assert parsed["schema_version"] == SCHEMA_VERSION
        assert parsed["accounts"][0]["name"] == "Acme 401k"


def target_date_allocation_of(account: Account) -> TargetDateAllocation:
    return account.get_holding(FundType.TARGET_DATE).target_date_allocation


MALFORMED = {
    "accounts is not a list": {"schema_version": 2, "accounts": 7},
    "account is not an object": {"schema_version": 2, "accounts": ["nope"]},
    "holdings is not a list": {
        "schema_version": 2,
        "accounts": [
            {
                "account_type": "Roth IRA",
                "name": "X",
                "tax_treatment": "tax_advantaged",
                "holdings": 3,
            }
        ],
    },
    "holding is not an object": {
        "schema_version": 2,
        "accounts": [
            {
                "account_type": "Roth IRA",
                "name": "X",
                "tax_treatment": "tax_advantaged",
                "holdings": ["nope"],
            }
        ],
    },
    "holding name is not a string": {
        "schema_version": 2,
        "accounts": [
            {
                "account_type": "Roth IRA",
                "name": "X",
                "tax_treatment": "tax_advantaged",
                "holdings": [{"fund_type": "us_stock", "name": ["VTI"], "value": "10"}],
            }
        ],
    },
    "allocation is not an object": {
        "schema_version": 2,
        "accounts": [
            {
                "account_type": "Roth IRA",
                "name": "X",
                "tax_treatment": "tax_advantaged",
                "holdings": [
                    {
                        "fund_type": "target_date",
                        "name": "Target 2050",
                        "value": "10",
                        "target_date_allocation": "60/20/20",
                    }
                ],
            }
        ],
    },
    "percentage is not a string": {"schema_version": 2, "stock_pct": ["80"], "accounts": []},
    "rebalance band is not a number": {
        "schema_version": SCHEMA_VERSION,
        "rebalance_band_pct": "not-a-number",
        "accounts": [],
    },
    "relative rebalance band is not a number": {
        "schema_version": SCHEMA_VERSION,
        "rebalance_relative_band_pct": ["25"],
        "accounts": [],
    },
    "account name is null": {
        "schema_version": 2,
        "accounts": [
            {
                "account_type": "Roth IRA",
                "name": None,
                "tax_treatment": "tax_advantaged",
                "holdings": [],
            }
        ],
    },
}


class TestMalformedShapes:
    """Valid JSON of the wrong shape must still come back as PersistenceError.
    cli.run() recovers from that by warning and starting blank; any other
    exception takes the whole run down over a file the user can hand-edit."""


    @pytest.mark.parametrize("description", sorted(MALFORMED))
    def test_wrong_shape_raises_persistence_error(self, description, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps(MALFORMED[description]))
        with pytest.raises(PersistenceError):
            load_config(path)

    @pytest.mark.parametrize("description", sorted(MALFORMED))
    def test_wrong_shape_raises_persistence_error_via_v1_too(self, description, tmp_path):
        """The v1 upgrade runs before any of these checks, so it has to survive
        the same garbage rather than being the thing that raises."""
        payload = dict(MALFORMED[description])
        payload["schema_version"] = 1
        path = tmp_path / "config.json"
        path.write_text(json.dumps(payload))
        with pytest.raises(PersistenceError):
            load_config(path)

    def test_specific_message_survives_the_catch_all(self, tmp_path):
        """The backstop must not bury the message that names what's wrong."""
        path = tmp_path / "config.json"
        path.write_text(json.dumps(MALFORMED["holding is not an object"]))
        with pytest.raises(PersistenceError, match="Invalid holding in config"):
            load_config(path)


MIXED = {
    "schema_version": 2,
    "accounts": [
        {
            "account_type": "Roth 401(k)",
            "name": "Acme 401k",
            "tax_treatment": "tax_advantaged",
            "holdings": [
                {"fund_type": "us_stock", "name": "VTI", "value": "6000"},
                {
                    "fund_type": "target_date",
                    "name": "Target 2050",
                    "value": "3000",
                    "target_date_allocation": {
                        "us_stock_pct": "60",
                        "international_stock_pct": "20",
                        "bond_pct": "20",
                    },
                },
            ],
        }
    ],
}


class TestMixedAccountsAreNoLongerLoadable:
    """Accounts used to be allowed to hold a target-date fund alongside
    individual funds. A config saved back then is now invalid, and the point
    of these tests is that it says so clearly instead of failing obscurely --
    cli.run() surfaces the message and starts blank."""


    def test_message_names_the_account_and_the_rule(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps(MIXED))
        with pytest.raises(PersistenceError, match="one or the other"):
            load_config(path)
        with pytest.raises(PersistenceError, match="Acme 401k"):
            load_config(path)

    def test_a_v1_file_with_a_mixed_account_fails_the_same_way(self, tmp_path):
        """The upgrade renames without validating, so a v1 mix reaches the
        same check rather than dying somewhere less legible."""
        payload = json.loads(json.dumps(MIXED))
        payload["schema_version"] = 1
        holdings = payload["accounts"][0]["holdings"]
        holdings[0] = {"fund_type": "domestic_equity", "name": "VTI", "balance": "6000"}
        holdings[1] = {
            "fund_type": "tdf",
            "name": "Target 2050",
            "balance": "3000",
            "tdf_allocation": {
                "domestic_equity_pct": "60",
                "international_equity_pct": "20",
                "bond_pct": "20",
            },
        }
        path = tmp_path / "config.json"
        path.write_text(json.dumps(payload))
        with pytest.raises(PersistenceError, match="one or the other"):
            load_config(path)


class TestSchemaV1Migration:
    """v1 named the fund types and the per-holding amount after the academic
    terms; v2 uses the words the CLI prints. Files written by v1 must keep
    loading, and must come back with the v2 spelling."""

    def _v1_payload(self) -> dict:
        return {
            "schema_version": 1,
            "stock_pct": "80",
            "bond_pct": "20",
            "vt_us_pct": "62.0",
            "vt_as_of": "2026-07-31",
            "balances_as_of": "2026-08-01",
            "accounts": [
                {
                    "account_type": "Roth IRA",
                    "name": "Acme Roth",
                    "tax_treatment": "tax_advantaged",
                    "holdings": [
                        {"fund_type": "domestic_equity", "name": "VTI", "balance": "6000"},
                        {"fund_type": "international_equity", "name": "VXUS", "balance": "2000"},
                        {"fund_type": "domestic_bond", "name": "BND", "balance": "1000"},
                        {"fund_type": "cash", "name": "", "balance": "500"},
                    ],
                },
                {
                    "account_type": "Roth 401(k)",
                    "name": "Acme 401k",
                    "tax_treatment": "tax_advantaged",
                    "holdings": [
                        {
                            "fund_type": "tdf",
                            "name": "Target 2050",
                            "balance": "3000",
                            "tdf_allocation": {
                                "domestic_equity_pct": "60",
                                "international_equity_pct": "20",
                                "bond_pct": "20",
                            },
                        },
                    ],
                },
            ],
        }

    def test_v1_file_loads_with_fund_types_and_values_translated(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps(self._v1_payload()))

        config = load_config(path)

        assert config.schema_version == SCHEMA_VERSION
        assert config.values_as_of == "2026-08-01"
        account, target_date_account = config.accounts
        assert account.total_value() == Decimal(9500)
        assert account.available_cash() == Decimal(500)
        assert account.get_holding(FundType.US_STOCK).value == Decimal(6000)
        assert account.get_holding(FundType.INTERNATIONAL_STOCK).name == "VXUS"
        assert account.get_holding(FundType.US_BOND).value == Decimal(1000)
        allocation = target_date_account.get_holding(FundType.TARGET_DATE).target_date_allocation
        assert allocation.us_stock_pct == Decimal(60)
        assert allocation.international_stock_pct == Decimal(20)

    def test_resaving_a_migrated_config_writes_the_current_version(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps(self._v1_payload()))

        save_config(path, load_config(path))

        written = json.loads(path.read_text())
        assert written["schema_version"] == SCHEMA_VERSION
        assert "balances_as_of" not in written
        holdings = {h["fund_type"]: h for h in written["accounts"][0]["holdings"]}
        assert set(holdings) == {"us_stock", "international_stock", "us_bond", "cash"}
        assert holdings["us_stock"]["value"] == "6000"
        target_date = written["accounts"][1]["holdings"][0]
        assert target_date["fund_type"] == "target_date"
        assert "target_date_allocation" in target_date

    def test_migration_does_not_mutate_the_callers_dict(self):
        payload = self._v1_payload()
        config_from_dict(payload)
        assert payload["schema_version"] == 1
        assert payload["accounts"][0]["holdings"][0]["fund_type"] == "domestic_equity"
        assert payload["accounts"][0]["holdings"][0]["balance"] == "6000"

    def test_corrupt_v1_file_still_reports_a_persistence_error(self, tmp_path):
        payload = self._v1_payload()
        payload["accounts"][0]["holdings"][0].pop("balance")
        path = tmp_path / "config.json"
        path.write_text(json.dumps(payload))
        with pytest.raises(PersistenceError, match="Invalid holding"):
            load_config(path)

    def test_non_dict_entries_are_passed_through_to_the_normal_parse(self):
        """Garbage in a v1 file must fail the way the same garbage in a v2 file
        fails, so the migration is never what a user sees blamed for it. The
        upgrade therefore leaves anything that isn't a dict alone."""
        for mangle in (
            lambda p: p.__setitem__("accounts", ["not an account"]),
            lambda p: p["accounts"][0].__setitem__("holdings", ["not a holding"]),
            lambda p: p["accounts"][1]["holdings"][0].__setitem__("tdf_allocation", "not a dict"),
        ):
            v1 = self._v1_payload()
            mangle(v1)
            v2 = _upgrade_v1(v1)
            v2["schema_version"] = 2

            with pytest.raises(Exception) as from_v1:
                config_from_dict(v1)
            with pytest.raises(Exception) as from_v2:
                config_from_dict(v2)
            assert type(from_v1.value) is type(from_v2.value)

    def test_unknown_future_schema_version_is_rejected(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"schema_version": 99, "accounts": []}))
        with pytest.raises(PersistenceError, match="Unsupported config schema_version"):
            load_config(path)


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
                    "schema_version": 2,
                    # missing tax_treatment
                    "accounts": [{"account_type": "Roth IRA", "name": "X"}],
                }
            )
        )
        with pytest.raises(PersistenceError, match="Invalid account"):
            load_config(path)

    def test_unparseable_stock_pct_raises(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps({"schema_version": 2, "stock_pct": "not-a-number", "accounts": []})
        )
        with pytest.raises(PersistenceError, match="Could not parse 'stock_pct'"):
            load_config(path)

    def test_incomplete_target_date_allocation_raises(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "accounts": [
                        {
                            "account_type": "Roth IRA",
                            "name": "X",
                            "tax_treatment": "tax_advantaged",
                            "holdings": [
                                {
                                    "fund_type": "target_date",
                                    "name": "Target 2050",
                                    "value": "1000",
                                    # missing "bond_pct"
                                    "target_date_allocation": {
                                        "us_stock_pct": "60",
                                        "international_stock_pct": "20",
                                    },
                                }
                            ],
                        }
                    ],
                }
            )
        )
        with pytest.raises(PersistenceError, match="Invalid target_date_allocation"):
            load_config(path)

    def test_holding_failing_model_validation_raises(self, tmp_path):
        # An empty name is valid JSON but rejected by Holding's own validation
        # for a non-cash fund type -- this should surface as PersistenceError,
        # not an uncaught ValueError.
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "accounts": [
                        {
                            "account_type": "Roth IRA",
                            "name": "X",
                            "tax_treatment": "tax_advantaged",
                            "holdings": [
                                {"fund_type": "us_stock", "name": "", "value": "100"}
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
                    "schema_version": 2,
                    "accounts": [
                        {
                            "account_type": "Roth IRA",
                            "name": "X",
                            "tax_treatment": "tax_advantaged",
                            "holdings": [{"fund_type": "bogus", "name": "VTI", "value": "100"}],
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


class TestSchemaV2Migration:
    """v2 had a single `tax_advantaged` treatment. v3 splits it, because
    which shelter an account is decides whether bonds belong there. The
    account's own `account_type` is what says which."""

    def _v2_payload(self, account_type="Roth IRA") -> dict:
        return {
            "schema_version": 2,
            "stock_pct": "80",
            "bond_pct": "20",
            "vt_us_pct": "62.0",
            "vt_as_of": "2026-07-31",
            "values_as_of": "2026-08-01",
            "accounts": [
                {
                    "account_type": account_type,
                    "name": "Shelter",
                    "tax_treatment": "tax_advantaged",
                    "holdings": [{"fund_type": "us_stock", "name": "VTI", "value": "6000"}],
                },
                {
                    "account_type": "Taxable Brokerage",
                    "name": "Brokerage",
                    "tax_treatment": "taxable",
                    "holdings": [{"fund_type": "us_stock", "name": "VTI", "value": "4000"}],
                },
            ],
        }

    def test_a_v2_file_walks_every_hop_to_the_current_version(self, tmp_path):
        """One hop at a time: v2 -> _upgrade_v2 -> v3 -> _upgrade_v3 -> v4.
        The taxable account carries the v3 spelling of its type, so this
        fails if the chain stops early."""
        path = tmp_path / "config.json"
        path.write_text(json.dumps(self._v2_payload("Roth IRA")))
        config = load_config(path)
        assert config.schema_version == SCHEMA_VERSION == 4
        assert config.accounts[1].account_type == "Brokerage"
        assert config.accounts[1].tax_treatment == TaxTreatment.TAXABLE

    def test_v4_renames_the_taxable_brokerage_account_type(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({
            "schema_version": 3,
            "accounts": [
                {
                    "account_type": "Taxable Brokerage",
                    "name": "B",
                    "tax_treatment": "taxable",
                    "holdings": [{"fund_type": "us_stock", "name": "VTI", "value": "1000"}],
                },
                {
                    "account_type": "Other",
                    "name": "O",
                    "tax_treatment": "tax_deferred",
                    "holdings": [{"fund_type": "us_stock", "name": "VTI", "value": "1000"}],
                },
            ],
        }))
        config = load_config(path)
        assert config.accounts[0].account_type == "Brokerage"
        # An account type the rename does not know is left exactly as it is.
        assert config.accounts[1].account_type == "Other"

    def test_the_v3_hop_does_not_mutate_the_callers_payload(self, tmp_path):
        """Every hop copies at each level, so a load that fails later never
        leaves the caller's parsed JSON half-renamed."""
        payload = {
            "schema_version": 3,
            "accounts": [{
                "account_type": "Taxable Brokerage",
                "name": "B",
                "tax_treatment": "taxable",
                "holdings": [{"fund_type": "us_stock", "name": "VTI", "value": "1000"}],
            }],
        }
        config_from_dict(payload)
        assert payload["accounts"][0]["account_type"] == "Taxable Brokerage"

    def test_a_roth_becomes_tax_free(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps(self._v2_payload("Roth IRA")))
        config = load_config(path)
        assert config.schema_version == SCHEMA_VERSION
        assert config.accounts[0].tax_treatment == TaxTreatment.TAX_FREE

    def test_a_traditional_401k_becomes_tax_deferred(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps(self._v2_payload("Traditional 401(k)")))
        assert load_config(path).accounts[0].tax_treatment == TaxTreatment.TAX_DEFERRED

    def test_an_hsa_becomes_tax_free(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps(self._v2_payload("HSA")))
        assert load_config(path).accounts[0].tax_treatment == TaxTreatment.TAX_FREE

    def test_an_unrecognized_account_type_becomes_tax_deferred(self, tmp_path):
        """An "Other" account's v2 answer was a yes/no that never recorded
        which shelter it was. Tax-deferred is the conservative guess: bonds
        fill it first, which is where they belong."""
        path = tmp_path / "config.json"
        path.write_text(json.dumps(self._v2_payload("Other")))
        assert load_config(path).accounts[0].tax_treatment == TaxTreatment.TAX_DEFERRED

    def test_a_taxable_account_is_left_alone(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps(self._v2_payload()))
        assert load_config(path).accounts[1].tax_treatment == TaxTreatment.TAXABLE

    def test_a_v2_file_has_no_band_so_none_is_reported(self, tmp_path):
        """Absent means "never chosen", so the prompt offers its own default
        rather than a guess dressed up as the user's saved answer."""
        path = tmp_path / "config.json"
        path.write_text(json.dumps(self._v2_payload()))
        assert load_config(path).rebalance_band_pct is None

    def test_upgrade_does_not_mutate_the_caller_s_payload(self):
        payload = self._v2_payload()
        _upgrade_v2(payload)
        assert payload["accounts"][0]["tax_treatment"] == "tax_advantaged"
        assert payload["schema_version"] == 2

    def test_a_v1_file_walks_all_the_way_to_the_current_version(self, tmp_path):
        """v1 carries v2's single treatment too, so the two upgrades chain."""
        path = tmp_path / "config.json"
        path.write_text(json.dumps({
            "schema_version": 1,
            "balances_as_of": "2026-08-01",
            "accounts": [{
                "account_type": "Roth IRA",
                "name": "Roth",
                "tax_treatment": "tax_advantaged",
                "holdings": [{"fund_type": "domestic_equity", "name": "VTI", "balance": "6000"}],
            }],
        }))
        config = load_config(path)
        assert config.schema_version == SCHEMA_VERSION
        assert config.accounts[0].tax_treatment == TaxTreatment.TAX_FREE
        assert config.accounts[0].get_holding(FundType.US_STOCK).value == Decimal(6000)


class TestRebalanceBandPersistence:
    def test_band_survives_a_round_trip(self, tmp_path):
        path = tmp_path / "config.json"
        save_config(path, PersistedConfig(rebalance_band_pct=Decimal("5.0")))
        assert load_config(path).rebalance_band_pct == Decimal("5.0")

    def test_the_relative_half_survives_a_round_trip(self, tmp_path):
        path = tmp_path / "config.json"
        save_config(
            path,
            PersistedConfig(
                rebalance_band_pct=Decimal("5.0"),
                rebalance_relative_band_pct=Decimal("25.0"),
            ),
        )
        assert load_config(path).rebalance_relative_band_pct == Decimal("25.0")

    def test_a_file_saved_before_the_relative_half_existed_reports_none(self, tmp_path):
        """It needs no schema hop -- an absent key means "never chosen"
        exactly as an absent band does, and step 2 offers the default."""
        path = tmp_path / "config.json"
        path.write_text(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "rebalance_band_pct": "5.0",
            "accounts": [],
        }))
        config = load_config(path)
        assert config.rebalance_band_pct == Decimal("5.0")
        assert config.rebalance_relative_band_pct is None

    def test_a_band_of_zero_round_trips_as_zero_not_as_absent(self, tmp_path):
        """Zero is a real answer -- "rebalance me to the exact target" -- and
        must not come back as "never chosen"."""
        path = tmp_path / "config.json"
        save_config(path, PersistedConfig(rebalance_band_pct=Decimal(0)))
        assert load_config(path).rebalance_band_pct == Decimal(0)
