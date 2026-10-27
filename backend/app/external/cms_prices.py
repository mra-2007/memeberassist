"""Medicare benchmark prices from the CMS Physician Fee Schedule.

Why this source
--------------
The project specification names the CMS dataset *"Medicare Physician & Other
Practitioners - by Geography and Service"*, reached through
``https://data.cms.gov/data-api/v1/dataset/<uuid>/data``. That host answers
**HTTP 403 (Akamai "Access Denied")** from this network on every path we need,
including ``/data.json``, ``/data-api/*`` and the dataset landing pages, with
any User-Agent and from both httpx and curl. Only ``/provider-data/*`` (a
different catalogue, holding provider *quality* measures) responds. The block
is recorded as a blocker in ``PROGRESS.md``.

Rather than write code against a response shape nobody has seen -- which
project rule 2.1.8 forbids -- this module uses a different **real, verified**
CMS source: the **PFS Relative Value Files**, published on ``www.cms.gov``,
which that host serves normally.

What the file gives us
----------------------
``PPRRVU<year>_<release>_nonQPP.csv`` lists, for every HCPCS/CPT code, the
work, practice-expense and malpractice RVUs plus the year's conversion factor.
The national Medicare allowed amount follows CMS's own published formula::

    allowed = total RVU x conversion factor

with national geographic indices of 1.0. Medicare then pays 80% of that and
the beneficiary owes the remaining 20% after their Part B deductible, so::

    payment = allowed x 0.80

Worked check against the 2026 January release, code 73721 (MRI, knee):
total non-facility RVU 6.12 x conversion factor 33.4009 = **$204.41**.

What it does not give us
------------------------
No average **submitted charge**. The utilization dataset publishes that; the
fee schedule does not. ``procedure_prices.avg_submitted_charge`` is therefore
left NULL, and the cost estimator produces a single benchmark estimate instead
of a low-high range, saying so in the answer. Once the blocked dataset is
reachable the column can be filled and the range appears automatically.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import re
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urljoin

from app.external.cache import ExternalApiError, fetch_bytes, fetch_json

RVU_INDEX_URL = (
    "https://www.cms.gov/medicare/payment/fee-schedules/physician/pfs-relative-value-files"
)

#: The specified-but-unreachable source, kept for documentation and for a
#: future run from an unblocked network.
CMS_UTILIZATION_CATALOGUE_URL = "https://data.cms.gov/data.json"
CMS_UTILIZATION_DATASET_TITLE = (
    "Medicare Physician & Other Practitioners - by Geography and Service"
)

#: Medicare Part B pays 80% of the approved amount after the deductible.
MEDICARE_PAYMENT_SHARE = Decimal("0.80")

#: Status codes that are payable under the fee schedule.
#: A = active, R = restricted, T = paid only when no other service is billed.
PAYABLE_STATUS_CODES = frozenset({"A", "R", "T"})

logger = logging.getLogger(__name__)

# Column positions in PPRRVU. The header spans four rows, so positions are
# fixed and then verified against the single-row header that starts "HCPCS".
COL_HCPCS = 0
COL_MOD = 1
COL_DESCRIPTION = 2
COL_STATUS = 3
COL_WORK_RVU = 5
COL_NONFAC_PE_RVU = 6
COL_NONFAC_NA = 7
COL_FAC_PE_RVU = 8
COL_FAC_NA = 9
COL_MP_RVU = 10
COL_NONFAC_TOTAL = 11
COL_FAC_TOTAL = 12
COL_CONV_FACTOR = 25

EXPECTED_HEADER = {
    COL_HCPCS: "HCPCS",
    COL_MOD: "MOD",
    COL_DESCRIPTION: "DESCRIPTION",
    COL_WORK_RVU: "RVU",
    COL_NONFAC_TOTAL: "TOTAL",
    COL_FAC_TOTAL: "TOTAL",
    COL_CONV_FACTOR: "FACTOR",
}


class PriceSourceError(RuntimeError):
    """The price source could not be reached or parsed."""


@dataclass(frozen=True, slots=True)
class PriceRow:
    """One benchmark price for a code in one place of service."""

    hcpcs_code: str
    description: str
    place_of_service: str
    avg_allowed: Decimal
    avg_payment: Decimal
    avg_submitted_charge: Decimal | None
    total_rvu: Decimal
    conversion_factor: Decimal
    data_year: int
    source: str
    source_url: str
    derivation: str
    retrieved_on: dt.date


def _money(value: Decimal) -> Decimal:
    """Round to cents the way a statement would."""
    return value.quantize(Decimal("0.01"))


def _decimal(raw: str) -> Decimal | None:
    """Parse an RVU or factor cell, tolerating blanks and stray spaces."""
    text = (raw or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except (ArithmeticError, ValueError):
        return None


def find_rvu_release(year: int) -> tuple[str, str]:
    """Find the current-year RVU zip on cms.gov.

    Returns:
        ``(label, zip_url)`` for the first (January, "A") release of ``year``.

    Raises:
        PriceSourceError: when no release page or zip can be found.
    """
    suffix = f"{year % 100:02d}"
    try:
        index_html = _get_text(RVU_INDEX_URL)
    except ExternalApiError as exc:
        raise PriceSourceError(f"cannot reach {RVU_INDEX_URL}: {exc}") from exc

    pattern = re.compile(
        rf'href="([^"]*pfs-relative-value-files/rvu{suffix}a[^"]*)"', re.IGNORECASE
    )
    match = pattern.search(index_html)
    if not match:
        raise PriceSourceError(
            f"no RVU{suffix}A release listed on {RVU_INDEX_URL}; check the plan year"
        )
    release_url = urljoin(RVU_INDEX_URL, match.group(1))

    release_html = _get_text(release_url)
    zip_match = re.search(r'href="([^"]+\.zip)"', release_html, re.IGNORECASE)
    if not zip_match:
        raise PriceSourceError(f"no .zip link on {release_url}")
    return f"RVU{suffix}A", urljoin(release_url, zip_match.group(1))


def _get_text(url: str) -> str:
    """Fetch an HTML page as text (cms.gov pages are small and cacheable)."""
    return fetch_bytes(url, timeout=120.0).decode("utf-8", errors="replace")


def _extract_pprrvu(archive: bytes) -> tuple[str, str]:
    """Pull the non-QPP PPRRVU CSV out of an RVU zip.

    Returns:
        ``(member_name, csv_text)``.
    """
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        candidates = [
            name
            for name in zf.namelist()
            if "PPRRVU" in name.upper() and name.upper().endswith(".CSV")
        ]
        # The nonQPP file is the plain fee-schedule view; prefer it.
        candidates.sort(key=lambda n: ("NONQPP" not in n.upper().replace("_", ""), n))
        if not candidates:
            raise PriceSourceError("RVU archive contains no PPRRVU CSV")
        name = candidates[0]
        # CMS writes these files in Latin-1, not UTF-8.
        return name, zf.read(name).decode("latin-1")


def _locate_header(rows: list[list[str]]) -> int:
    """Index of the row whose first cell is ``HCPCS``.

    Raises:
        PriceSourceError: if the layout no longer matches what this
            parser was written against, rather than silently mis-reading prices.
    """
    for index, row in enumerate(rows[:40]):
        if row and row[0].strip().upper() == "HCPCS":
            for column, expected in EXPECTED_HEADER.items():
                actual = row[column].strip().upper() if column < len(row) else ""
                if actual != expected:
                    raise PriceSourceError(
                        "PPRRVU column layout changed: expected "
                        f"{expected!r} at column {column}, found {actual!r}. "
                        "Re-check the file before trusting any price."
                    )
            return index
    raise PriceSourceError("no HCPCS header row found in PPRRVU CSV")


def parse_pprrvu(
    csv_text: str,
    wanted_codes: set[str],
    *,
    data_year: int,
    source_url: str,
    retrieved_on: dt.date,
) -> list[PriceRow]:
    """Turn a PPRRVU CSV into benchmark price rows for the requested codes.

    Only the **global** row for each code is used (empty modifier), which is
    the complete service a member is billed for. The ``26`` (professional) and
    ``TC`` (technical) component rows are skipped.

    A place of service is emitted only when the file says the code is payable
    there: an ``NA`` indicator means the service is effectively never performed
    in that setting, so pricing it would be misleading.
    """
    rows = list(csv.reader(io.StringIO(csv_text)))
    header_index = _locate_header(rows)
    wanted = {code.strip().upper() for code in wanted_codes}

    out: list[PriceRow] = []
    seen: set[tuple[str, str]] = set()

    for row in rows[header_index + 1 :]:
        if len(row) <= COL_CONV_FACTOR:
            continue
        code = row[COL_HCPCS].strip().upper()
        if code not in wanted:
            continue
        if row[COL_MOD].strip():
            continue  # component row, not the global service
        if row[COL_STATUS].strip().upper() not in PAYABLE_STATUS_CODES:
            continue

        conversion_factor = _decimal(row[COL_CONV_FACTOR])
        if conversion_factor is None or conversion_factor <= 0:
            continue
        description = row[COL_DESCRIPTION].strip()

        for place, total_column, na_column in (
            ("office", COL_NONFAC_TOTAL, COL_NONFAC_NA),
            ("facility", COL_FAC_TOTAL, COL_FAC_NA),
        ):
            if (code, place) in seen:
                continue
            if row[na_column].strip().upper() == "NA":
                continue
            total_rvu = _decimal(row[total_column])
            if total_rvu is None or total_rvu <= 0:
                continue

            allowed = _money(total_rvu * conversion_factor)
            out.append(
                PriceRow(
                    hcpcs_code=code,
                    description=description,
                    place_of_service=place,
                    avg_allowed=allowed,
                    avg_payment=_money(allowed * MEDICARE_PAYMENT_SHARE),
                    avg_submitted_charge=None,
                    total_rvu=total_rvu,
                    conversion_factor=conversion_factor,
                    data_year=data_year,
                    source="cms_pfs_rvu",
                    source_url=source_url,
                    derivation=(
                        f"allowed = total RVU {total_rvu} x conversion factor "
                        f"{conversion_factor} (national geographic indices = 1.0); "
                        f"payment = allowed x {MEDICARE_PAYMENT_SHARE} "
                        "(Medicare Part B pays 80% after the deductible)"
                    ),
                    retrieved_on=retrieved_on,
                )
            )
            seen.add((code, place))
    return out


def fetch_benchmark_prices(
    codes: set[str], year: int, *, retrieved_on: dt.date | None = None
) -> tuple[list[PriceRow], str]:
    """Download the PFS RVU file for ``year`` and price ``codes``.

    Returns:
        ``(rows, source_url)``.
    """
    label, zip_url = find_rvu_release(year)
    logger.info("using PFS release %s from %s", label, zip_url)
    archive = fetch_bytes(zip_url)
    member, csv_text = _extract_pprrvu(archive)
    logger.info("parsing %s (%d characters)", member, len(csv_text))
    rows = parse_pprrvu(
        csv_text,
        codes,
        data_year=year,
        source_url=zip_url,
        retrieved_on=retrieved_on or dt.date.today(),
    )
    return rows, zip_url


# ---------------------------------------------------------------------------
# Clinical Laboratory Fee Schedule
# ---------------------------------------------------------------------------
#: CMS publishes the CLFS quarterly at a predictable path, verified by request:
#: 26clabq1.zip .. 26clabq4.zip for 2026. The linked "CLFS Files" page builds
#: its list in the browser, so the files are addressed directly.
CLFS_URL_TEMPLATE = "https://www.cms.gov/files/zip/{yy}clabq{quarter}.zip"
CLFS_LANDING_PAGE = (
    "https://www.cms.gov/medicare/payment/fee-schedules/clinical-laboratory-fee-schedule-clfs/files"
)

#: Clinical diagnostic laboratory tests carry no Part B coinsurance or
#: deductible, so Medicare pays the full fee-schedule rate.
CLFS_PAYMENT_SHARE = Decimal("1.00")


def _parse_clfs(
    csv_text: str,
    wanted_codes: set[str],
    *,
    data_year: int,
    source_url: str,
    retrieved_on: dt.date,
) -> list[PriceRow]:
    """Parse a CLFS quarterly CSV.

    Layout, confirmed against the 2026 Q1 file::

        YEAR,HCPCS,MOD,EFF_DATE,INDICATOR,RATE,SHORTDESC,LONGDESC,EXTENDEDLONGDESC

    Rows are repeated with a ``QW`` modifier (CLIA-waived testing) at the same
    rate; only the unmodified row is kept.
    """
    rows = list(csv.reader(io.StringIO(csv_text)))
    header_index = next(
        (i for i, row in enumerate(rows[:40]) if row and row[0].strip().upper() == "YEAR"),
        None,
    )
    if header_index is None:
        raise PriceSourceError("no YEAR header row found in the CLFS CSV")
    header = [cell.strip().upper() for cell in rows[header_index]]
    try:
        col_code = header.index("HCPCS")
        col_mod = header.index("MOD")
        col_rate = header.index("RATE")
        col_desc = header.index("SHORTDESC")
    except ValueError as exc:
        raise PriceSourceError(f"CLFS column layout changed: {header}") from exc

    wanted = {code.strip().upper() for code in wanted_codes}
    out: list[PriceRow] = []
    seen: set[str] = set()

    for row in rows[header_index + 1 :]:
        if len(row) <= max(col_rate, col_desc):
            continue
        code = row[col_code].strip().upper()
        if code not in wanted or code in seen:
            continue
        if row[col_mod].strip():
            continue  # QW (CLIA-waived) duplicate of the same rate
        rate = _decimal(row[col_rate])
        if rate is None or rate <= 0:
            continue
        seen.add(code)
        # Labs are not split by place of service on the CLFS; the single
        # national rate applies wherever the specimen is analysed.
        out.append(
            PriceRow(
                hcpcs_code=code,
                description=row[col_desc].strip(),
                place_of_service="office",
                avg_allowed=_money(rate),
                avg_payment=_money(rate * CLFS_PAYMENT_SHARE),
                avg_submitted_charge=None,
                total_rvu=Decimal("0"),
                conversion_factor=Decimal("0"),
                data_year=data_year,
                source="cms_clfs",
                source_url=source_url,
                derivation=(
                    "national Clinical Laboratory Fee Schedule rate; Medicare pays "
                    "100% because clinical diagnostic laboratory tests carry no "
                    "Part B coinsurance or deductible"
                ),
                retrieved_on=retrieved_on,
            )
        )
    return out


def fetch_lab_prices(
    codes: set[str], year: int, *, retrieved_on: dt.date | None = None
) -> tuple[list[PriceRow], str]:
    """Price laboratory codes from the Clinical Laboratory Fee Schedule.

    Codes carrying Physician Fee Schedule status ``X`` are statutorily excluded
    from that schedule and paid here instead -- all five of this project's lab
    codes are in that position.

    Later quarters supersede earlier ones, so the newest available quarter of
    ``year`` is used.
    """
    retrieved = retrieved_on or dt.date.today()
    suffix = f"{year % 100:02d}"
    errors: list[str] = []
    for quarter in (4, 3, 2, 1):
        url = CLFS_URL_TEMPLATE.format(yy=suffix, quarter=quarter)
        try:
            archive = fetch_bytes(url, timeout=300.0)
        except ExternalApiError as exc:
            errors.append(f"Q{quarter}: HTTP {exc.status}")
            continue
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            member = next((name for name in zf.namelist() if name.lower().endswith(".csv")), None)
            if member is None:
                errors.append(f"Q{quarter}: no CSV in archive")
                continue
            csv_text = zf.read(member).decode("latin-1")
        logger.info("using CLFS %s Q%d (%s)", year, quarter, member)
        return (
            _parse_clfs(
                csv_text,
                codes,
                data_year=year,
                source_url=url,
                retrieved_on=retrieved,
            ),
            url,
        )
    raise PriceSourceError(f"no CLFS quarter available for {year}: {'; '.join(errors)}")


def utilization_dataset_status() -> dict[str, object]:
    """Probe the specified CMS utilization dataset and report what happened.

    Called by ``fetch_prices.py`` so the blocker is re-tested on every run
    instead of being assumed permanent. Returns a plain dict rather than
    raising, because being blocked is an expected outcome here.
    """
    try:
        payload = fetch_json(CMS_UTILIZATION_CATALOGUE_URL, ttl=60 * 60, timeout=60.0)
    except ExternalApiError as exc:
        return {"reachable": False, "status": exc.status, "detail": exc.body[:200]}
    except Exception as exc:
        return {"reachable": False, "status": None, "detail": f"{type(exc).__name__}: {exc}"}

    datasets = payload.get("dataset", []) if isinstance(payload, dict) else []
    matches = [
        entry
        for entry in datasets
        if isinstance(entry, dict)
        and "physician" in str(entry.get("title", "")).lower()
        and "geography" in str(entry.get("title", "")).lower()
    ]
    return {"reachable": True, "status": 200, "matches": len(matches), "datasets": matches}
