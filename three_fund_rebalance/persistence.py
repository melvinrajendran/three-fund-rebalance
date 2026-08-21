"""Local JSON persistence for account/fund structure (and other reusable
inputs) between runs, so the user isn't retyping their account setup every
time. Dollar values are persisted too, as a "last known snapshot" with an
as-of date, but the interactive flow always re-offers them as an *editable
default* rather than silently trusting stale numbers -- see prompts.py.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from three_fund_rebalance.config import SCHEMA_VERSION
from three_fund_rebalance.models import (
    Account,
    FundType,
    Holding,
    TargetDateAllocation,
    TaxTreatment,
)


class PersistenceError(Exception):
    """Raised when a config file exists but can't be read as a valid
    portfolio configuration. Callers should catch this, warn the user, and
    fall back to a fresh/blank configuration rather than crash."""


@dataclass
class PersistedConfig:
    schema_version: int = SCHEMA_VERSION
    stock_pct: Decimal | None = None
    bond_pct: Decimal | None = None
    vt_us_pct: Decimal | None = None
    vt_as_of: str | None = None
    # Set whenever accounts (including values) are saved; shown to the
    # user so they know how stale a pre-filled value might be.
    values_as_of: str | None = None
    accounts: list[Account] = field(default_factory=list)


def _decimal_to_json(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _decimal_from_json(value: str | None, *, field_name: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise PersistenceError(f"Could not parse {field_name!r} as a number: {value!r}") from exc


def _target_date_allocation_to_dict(allocation: TargetDateAllocation) -> dict:
    return {
        "us_stock_pct": str(allocation.us_stock_pct),
        "international_stock_pct": str(allocation.international_stock_pct),
        "bond_pct": str(allocation.bond_pct),
    }


def _target_date_allocation_from_dict(data: dict) -> TargetDateAllocation:
    try:
        return TargetDateAllocation(
            us_stock_pct=Decimal(data["us_stock_pct"]),
            international_stock_pct=Decimal(data["international_stock_pct"]),
            bond_pct=Decimal(data["bond_pct"]),
        )
    except (KeyError, InvalidOperation, ValueError) as exc:
        raise PersistenceError(f"Invalid target_date_allocation in config: {data!r}") from exc


def _holding_to_dict(holding: Holding) -> dict:
    data = {"fund_type": holding.fund_type.value, "name": holding.name, "value": str(holding.value)}
    if holding.target_date_allocation is not None:
        data["target_date_allocation"] = _target_date_allocation_to_dict(holding.target_date_allocation)
    return data


def _holding_from_dict(data: dict) -> Holding:
    try:
        fund_type = FundType(data["fund_type"])
        value = Decimal(data["value"])
    except (KeyError, ValueError, InvalidOperation) as exc:
        raise PersistenceError(f"Invalid holding in config: {data!r}") from exc
    allocation_data = data.get("target_date_allocation")
    try:
        return Holding(
            fund_type=fund_type,
            name=data.get("name", ""),
            value=value,
            target_date_allocation=_target_date_allocation_from_dict(allocation_data) if allocation_data else None,
        )
    except ValueError as exc:
        raise PersistenceError(f"Invalid holding in config: {data!r}: {exc}") from exc


def _account_to_dict(account: Account) -> dict:
    return {
        "account_type": account.account_type,
        "name": account.name,
        "tax_treatment": account.tax_treatment.value,
        "holdings": [_holding_to_dict(h) for h in account.holdings],
    }


def _account_from_dict(data: dict) -> Account:
    try:
        tax_treatment = TaxTreatment(data["tax_treatment"])
        holdings = [_holding_from_dict(h) for h in data.get("holdings", [])]
        return Account(
            account_type=data["account_type"],
            name=data["name"],
            tax_treatment=tax_treatment,
            holdings=holdings,
        )
    except (KeyError, ValueError) as exc:
        raise PersistenceError(f"Invalid account in config: {data!r}: {exc}") from exc


def config_to_dict(config: PersistedConfig) -> dict:
    return {
        "schema_version": config.schema_version,
        "stock_pct": _decimal_to_json(config.stock_pct),
        "bond_pct": _decimal_to_json(config.bond_pct),
        "vt_us_pct": _decimal_to_json(config.vt_us_pct),
        "vt_as_of": config.vt_as_of,
        "values_as_of": config.values_as_of,
        "accounts": [_account_to_dict(a) for a in config.accounts],
    }


# What each v1 fund type is called in v2. v1 named the three asset classes
# after the academic terms ("domestic equity"); v2 uses the words a brokerage
# statement uses, matching what the CLI now prints.
_V1_FUND_TYPES = {
    "domestic_equity": "us_stock",
    "international_equity": "international_stock",
    "domestic_bond": "us_bond",
    "tdf": "target_date",
    "cash": "cash",
}

_V1_ALLOCATION_KEYS = {
    "domestic_equity_pct": "us_stock_pct",
    "international_equity_pct": "international_stock_pct",
    "bond_pct": "bond_pct",
}


def _upgrade_v1(data: dict) -> dict:
    """Translate a schema v1 payload into v2's spelling, without validating
    it -- anything still wrong is caught by the normal parse afterwards, so a
    corrupt v1 file reports the same error a corrupt v2 file would. Unknown
    fund types are passed through untouched for that reason.

    Copies at every level rather than mutating: `data` is the caller's parsed
    JSON, and a failed load must not leave it half-renamed.
    """
    upgraded = dict(data)
    upgraded["values_as_of"] = upgraded.pop("balances_as_of", None)
    accounts = []
    for account in upgraded.get("accounts") or []:
        if not isinstance(account, dict):
            accounts.append(account)
            continue
        account = dict(account)
        holdings = []
        for holding in account.get("holdings") or []:
            if not isinstance(holding, dict):
                holdings.append(holding)
                continue
            holding = dict(holding)
            holding["fund_type"] = _V1_FUND_TYPES.get(
                holding.get("fund_type"), holding.get("fund_type")
            )
            if "balance" in holding:
                holding["value"] = holding.pop("balance")
            allocation = holding.pop("tdf_allocation", None)
            if isinstance(allocation, dict):
                holding["target_date_allocation"] = {
                    _V1_ALLOCATION_KEYS.get(key, key): value for key, value in allocation.items()
                }
            elif allocation is not None:
                holding["target_date_allocation"] = allocation
            holdings.append(holding)
        account["holdings"] = holdings
        accounts.append(account)
    upgraded["accounts"] = accounts
    upgraded["schema_version"] = SCHEMA_VERSION
    return upgraded


def config_from_dict(data: dict) -> PersistedConfig:
    schema_version = data.get("schema_version")
    if schema_version == 1:
        data = _upgrade_v1(data)
        schema_version = data["schema_version"]
    if schema_version != SCHEMA_VERSION:
        raise PersistenceError(
            f"Unsupported config schema_version {schema_version!r} "
            f"(this version of three-fund-rebalance understands 1 and {SCHEMA_VERSION})"
        )
    return PersistedConfig(
        schema_version=schema_version,
        stock_pct=_decimal_from_json(data.get("stock_pct"), field_name="stock_pct"),
        bond_pct=_decimal_from_json(data.get("bond_pct"), field_name="bond_pct"),
        vt_us_pct=_decimal_from_json(data.get("vt_us_pct"), field_name="vt_us_pct"),
        vt_as_of=data.get("vt_as_of"),
        values_as_of=data.get("values_as_of"),
        accounts=[_account_from_dict(a) for a in data.get("accounts", [])],
    )


def load_config(path: Path) -> PersistedConfig:
    """Return a fresh/blank PersistedConfig if `path` doesn't exist yet.
    Raises PersistenceError if it exists but isn't valid -- callers should
    catch this, warn the user, and proceed with a blank config rather than
    crash the whole CLI over a corrupt file."""
    if not path.exists():
        return PersistedConfig()
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise PersistenceError(f"Could not read config file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PersistenceError(f"Config file {path} does not contain a JSON object")
    return config_from_dict(raw)


def save_config(path: Path, config: PersistedConfig) -> None:
    """Write `config` to `path` as pretty JSON, creating parent directories
    as needed. Writes to a temp file in the same directory and renames it
    into place so a crash mid-write can't corrupt an existing config."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(config_to_dict(config), indent=2, sort_keys=False) + "\n"
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
        os.replace(tmp_path, path)
    finally:
        # If os.replace already succeeded, tmp_path no longer exists and
        # this is a no-op; if we raised before that, clean up after ourselves.
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
