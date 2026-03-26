from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

DEFAULT_OUTPUT_DIR = Path("data/akshare_sina_minute")
DEFAULT_QLIB_STYLE_OUTPUT_DIR = Path("data/qlib_csv_minute_sina")
DEFAULT_STOCK_POOL_FILE = Path("data/ashare_stock_info.csv")
NUMERIC_COLUMNS = ("open", "high", "low", "close", "volume")
LOGGER = logging.getLogger("minute_data.akshare_sina")


@dataclass(frozen=True)
class DownloadConfig:
    period: str = "1"
    adjust: str = ""
    start_date: str | None = None
    end_date: str | None = None
    sleep_seconds: float = 1.5
    jitter_seconds: float = 0.5
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0
    output_dir: Path = DEFAULT_OUTPUT_DIR
    file_format: str = "parquet"
    append: bool = True
    qlib_style: bool = False
    log_level: str = "INFO"
    skip_empty: bool = True


def _configure_logging(level: str = "INFO") -> None:
    if LOGGER.handlers:
        LOGGER.setLevel(getattr(logging, level.upper(), logging.INFO))
        return

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(getattr(logging, level.upper(), logging.INFO))
    LOGGER.propagate = False


def _import_akshare():
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "akshare is required to download minute data. Install it in the target environment first."
        ) from exc
    return ak


def _sleep_with_jitter(base_seconds: float, jitter_seconds: float) -> None:
    sleep_for = max(0.0, base_seconds) + random.uniform(0.0, max(0.0, jitter_seconds))
    if sleep_for > 0:
        LOGGER.info("sleeping %.2fs before next request", sleep_for)
        time.sleep(sleep_for)


def _normalize_minute_frame(
    frame: pd.DataFrame,
    *,
    symbol: str,
    period: str,
    adjust: str,
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["datetime", *NUMERIC_COLUMNS, "symbol", "period", "adjust"])

    normalized = frame.copy()
    normalized.columns = [str(column).strip().lower() for column in normalized.columns]
    if "datetime" not in normalized.columns and "day" in normalized.columns:
        normalized = normalized.rename(columns={"day": "datetime"})
    if "datetime" not in normalized.columns:
        raise ValueError(f"Unexpected columns returned for {symbol}: {list(frame.columns)}")

    normalized["datetime"] = pd.to_datetime(normalized["datetime"], errors="coerce")
    normalized = normalized.dropna(subset=["datetime"])

    for column in NUMERIC_COLUMNS:
        if column in normalized.columns:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")

    normalized["symbol"] = symbol
    normalized["period"] = period
    normalized["adjust"] = adjust

    ordered_columns = ["datetime"]
    ordered_columns.extend(column for column in NUMERIC_COLUMNS if column in normalized.columns)
    ordered_columns.extend(
        column
        for column in normalized.columns
        if column not in set(ordered_columns) | {"symbol", "period", "adjust"}
    )
    ordered_columns.extend(["symbol", "period", "adjust"])

    normalized = normalized[ordered_columns]
    normalized = normalized.sort_values("datetime").drop_duplicates(
        subset=["datetime", "symbol", "period", "adjust"],
        keep="last",
    )
    return normalized.reset_index(drop=True)


def _filter_frame_by_date_range(frame: pd.DataFrame, config: DownloadConfig) -> pd.DataFrame:
    if frame.empty:
        return frame

    filtered = frame.copy()
    start_ts = pd.Timestamp(config.start_date) if config.start_date else None
    end_ts = pd.Timestamp(config.end_date) if config.end_date else None

    if start_ts is not None:
        filtered = filtered[filtered["datetime"] >= start_ts]
    if end_ts is not None:
        # Date-only inputs include the whole day; explicit timestamps are respected as-is.
        if end_ts.time() == pd.Timestamp("00:00:00").time() and len(str(config.end_date or "")) <= 10:
            end_ts = end_ts + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        filtered = filtered[filtered["datetime"] <= end_ts]

    return filtered.reset_index(drop=True)


def _normalize_symbol(symbol: str) -> str:
    cleaned = symbol.strip()
    if not cleaned:
        raise ValueError("Empty symbol is not allowed")
    if len(cleaned) >= 8 and cleaned[:2].lower() in {"sh", "sz", "bj"}:
        return cleaned[:2].upper() + cleaned[2:]
    return cleaned.upper()


def _to_akshare_symbol(symbol: str) -> str:
    cleaned = symbol.strip()
    if not cleaned:
        raise ValueError("Empty symbol is not allowed")
    if len(cleaned) >= 8 and cleaned[:2].lower() in {"sh", "sz", "bj"}:
        return cleaned[:2].lower() + cleaned[2:]
    return cleaned.lower()


def fetch_symbol_minute(symbol: str, config: DownloadConfig | None = None) -> pd.DataFrame:
    """
    Download one symbol from akshare's Sina minute endpoint.

    Sina may reject high-frequency access, so this function applies retry backoff.
    """

    config = config or DownloadConfig()
    _configure_logging(config.log_level)
    ak = _import_akshare()
    last_error: Exception | None = None

    for attempt in range(1, config.max_retries + 1):
        try:
            LOGGER.info(
                "downloading symbol=%s period=%s adjust=%s attempt=%d/%d",
                symbol,
                config.period,
                config.adjust or "none",
                attempt,
                config.max_retries,
            )
            started_at = time.perf_counter()
            raw = ak.stock_zh_a_minute(
                symbol=_to_akshare_symbol(symbol),
                period=config.period,
                adjust=config.adjust,
            )
            normalized = _normalize_minute_frame(
                raw,
                symbol=symbol,
                period=config.period,
                adjust=config.adjust,
            )
            filtered = _filter_frame_by_date_range(normalized, config)
            if normalized.empty:
                LOGGER.warning("download returned no rows for symbol=%s", symbol)
            elif filtered.empty:
                LOGGER.warning(
                    "symbol=%s returned %d rows but 0 rows remain after date filtering; "
                    "stock_zh_a_minute only provides recent trading-day minute data, so your date range may be out of range",
                    symbol,
                    len(normalized),
                )
            LOGGER.info(
                "downloaded symbol=%s rows=%d filtered_rows=%d elapsed=%.2fs",
                symbol,
                len(normalized),
                len(filtered),
                time.perf_counter() - started_at,
            )
            return filtered
        except Exception as exc:  # pragma: no cover - depends on network and provider
            last_error = exc
            if attempt >= config.max_retries:
                break
            LOGGER.warning(
                "download failed for %s on attempt %d/%d: %s",
                symbol,
                attempt,
                config.max_retries,
                exc,
            )
            _sleep_with_jitter(config.retry_backoff_seconds * attempt, config.jitter_seconds)

    assert last_error is not None
    LOGGER.error("download failed for %s after %d attempts: %s", symbol, config.max_retries, last_error)
    raise RuntimeError(f"Failed to download {symbol} after {config.max_retries} attempts") from last_error


def _read_existing(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    if path.suffix == ".csv":
        header = pd.read_csv(path, nrows=0)
        parse_columns = [column for column in ("datetime", "date") if column in header.columns]
        return pd.read_csv(path, parse_dates=parse_columns)
    raise ValueError(f"Unsupported file format for {path}")


def _write_frame(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".parquet":
        frame.to_parquet(path, index=False)
        return
    if path.suffix == ".csv":
        frame.to_csv(path, index=False)
        return
    raise ValueError(f"Unsupported file format for {path}")


def _destination_path(symbol: str, config: DownloadConfig) -> Path:
    normalized_symbol = _normalize_symbol(symbol)
    extension = "parquet" if config.file_format == "parquet" else "csv"
    if config.qlib_style:
        return config.output_dir / f"{normalized_symbol}.csv"
    return config.output_dir / config.period / f"{normalized_symbol}.{extension}"


def _to_qlib_style(frame: pd.DataFrame) -> pd.DataFrame:
    qlib_frame = frame.copy()
    qlib_frame = qlib_frame.rename(columns={"datetime": "date"})
    qlib_frame["date"] = pd.to_datetime(qlib_frame["date"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")

    ordered_columns = ["date"]
    ordered_columns.extend(column for column in NUMERIC_COLUMNS if column in qlib_frame.columns)
    ordered_columns.extend(
        column
        for column in qlib_frame.columns
        if column not in set(ordered_columns) | {"symbol", "period", "adjust"}
    )
    return qlib_frame[ordered_columns]


def save_symbol_minute(
    frame: pd.DataFrame,
    *,
    symbol: str,
    config: DownloadConfig | None = None,
) -> Path:
    config = config or DownloadConfig()
    _configure_logging(config.log_level)
    output_path = _destination_path(symbol, config)

    if frame.empty and config.skip_empty:
        LOGGER.warning("skip saving empty frame for symbol=%s path=%s", symbol, output_path)
        return output_path

    normalized_frame = _to_qlib_style(frame) if config.qlib_style else frame
    merged = normalized_frame
    if config.append and output_path.exists():
        existing = _read_existing(output_path)
        if config.qlib_style:
            if "date" in existing.columns:
                existing["date"] = pd.to_datetime(existing["date"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
            merged = pd.concat([existing, normalized_frame], ignore_index=True)
            merged = merged.drop_duplicates(subset=["date"], keep="last").sort_values("date")
        else:
            merged = pd.concat([existing, normalized_frame], ignore_index=True)
            merged["datetime"] = pd.to_datetime(merged["datetime"], errors="coerce")
            merged = merged.dropna(subset=["datetime"]).sort_values("datetime").drop_duplicates(
                subset=["datetime", "symbol", "period", "adjust"],
                keep="last",
            )

    _write_frame(merged.reset_index(drop=True), output_path)
    LOGGER.info("saved symbol=%s rows=%d path=%s", symbol, len(merged), output_path)
    return output_path


def download_symbols(
    symbols: Sequence[str],
    config: DownloadConfig | None = None,
) -> dict[str, Path]:
    config = config or DownloadConfig()
    _configure_logging(config.log_level)
    saved_paths: dict[str, Path] = {}
    ordered_symbols = [symbol.strip() for symbol in symbols if symbol and symbol.strip()]
    total = len(ordered_symbols)
    LOGGER.info(
        "starting batch download symbols=%d period=%s qlib_style=%s output_dir=%s",
        total,
        config.period,
        config.qlib_style,
        config.output_dir,
    )

    for index, symbol in enumerate(ordered_symbols):
        LOGGER.info("processing %d/%d %s", index + 1, total, symbol)
        frame = fetch_symbol_minute(symbol, config=config)
        if frame.empty and config.skip_empty:
            LOGGER.warning("symbol=%s skipped because filtered result is empty", symbol)
        else:
            saved_paths[symbol] = save_symbol_minute(frame, symbol=symbol, config=config)
        if index < len(ordered_symbols) - 1:
            _sleep_with_jitter(config.sleep_seconds, config.jitter_seconds)

    LOGGER.info("batch download completed symbols=%d saved=%d", total, len(saved_paths))
    return saved_paths


def download_stock_pool(
    stock_pool_file: Path,
    config: DownloadConfig | None = None,
) -> dict[str, Path]:
    config = config or DownloadConfig()
    _configure_logging(config.log_level)
    symbols = _unique_symbols(_load_symbols_from_file(stock_pool_file))
    LOGGER.info("loaded stock pool path=%s symbols=%d", stock_pool_file, len(symbols))
    return download_symbols(symbols, config=config)


def _load_symbols_from_file(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        separator = "\t" if suffix == ".tsv" else ","
        frame = pd.read_csv(path, sep=separator)
        normalized_columns = {str(column).strip().lower(): column for column in frame.columns}
        for candidate in ("qlib_symbol", "symbol", "code", "stock", "instrument"):
            if candidate in normalized_columns:
                source_column = normalized_columns[candidate]
                symbols = [str(value).strip() for value in frame[source_column].dropna().tolist() if str(value).strip()]
                if candidate == "symbol":
                    exchange_column = normalized_columns.get("exchange")
                    if exchange_column is not None:
                        converted: list[str] = []
                        for _, row in frame[[source_column, exchange_column]].dropna().iterrows():
                            code = str(row[source_column]).strip()
                            exchange = str(row[exchange_column]).strip().upper()
                            prefix = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}.get(exchange)
                            converted.append(f"{prefix}{code}" if prefix else code)
                        if converted:
                            symbols = converted
                return symbols

    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def _unique_symbols(symbols: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for symbol in symbols:
        if symbol not in seen:
            seen.add(symbol)
            ordered.append(symbol)
    return ordered


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download A-share minute data from akshare Sina endpoint.")
    parser.add_argument("--symbols", nargs="*", default=[], help="Stock symbols such as sh600000 sz000001.")
    parser.add_argument("--symbols-file", type=Path, help="Optional text file with one symbol per line.")
    parser.add_argument("--stock-pool-file", type=Path, help="Alias of --symbols-file for stock pool files.")
    parser.add_argument("--period", default="1", help="Minute period accepted by akshare, e.g. 1/5/15/30/60.")
    parser.add_argument("--adjust", default="", help="Adjustment mode forwarded to akshare.")
    parser.add_argument(
        "--start-date",
        help="Inclusive start date or datetime, e.g. 2026-03-01 or 2026-03-01 09:30:00.",
    )
    parser.add_argument(
        "--end-date",
        help="Inclusive end date or datetime, e.g. 2026-03-26 or 2026-03-26 15:00:00.",
    )
    parser.add_argument("--sleep-seconds", type=float, default=1.5, help="Base delay between symbols.")
    parser.add_argument("--jitter-seconds", type=float, default=0.5, help="Extra random jitter added to each delay.")
    parser.add_argument("--max-retries", type=int, default=3, help="Maximum retries per symbol.")
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=2.0,
        help="Backoff multiplier used after failed requests.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where downloaded files are written.",
    )
    parser.add_argument(
        "--format",
        choices=["parquet", "csv"],
        default="parquet",
        help="Output file format.",
    )
    parser.add_argument(
        "--no-append",
        action="store_true",
        help="Overwrite the target symbol file instead of merging with existing data.",
    )
    parser.add_argument(
        "--qlib-style",
        action="store_true",
        help="Save flat per-symbol CSV files similar to data/qlib_csv_daily_hfq.",
    )
    parser.add_argument(
        "--use-default-stock-pool",
        action="store_true",
        help=f"Use {DEFAULT_STOCK_POOL_FILE} as the stock pool file.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level for real-time download progress.",
    )
    parser.add_argument(
        "--keep-empty",
        action="store_true",
        help="Write empty files even when the filtered result has no data.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    symbol_values = list(args.symbols)
    pool_file = args.stock_pool_file or args.symbols_file
    if args.use_default_stock_pool or (not symbol_values and pool_file is None):
        pool_file = DEFAULT_STOCK_POOL_FILE
    if pool_file:
        symbol_values.extend(_load_symbols_from_file(pool_file))

    symbols = _unique_symbols(symbol_values)
    if not symbols:
        parser.error("Provide at least one symbol via --symbols or --symbols-file.")

    config = DownloadConfig(
        period=args.period,
        adjust=args.adjust,
        start_date=args.start_date,
        end_date=args.end_date,
        sleep_seconds=args.sleep_seconds,
        jitter_seconds=args.jitter_seconds,
        max_retries=args.max_retries,
        retry_backoff_seconds=args.retry_backoff_seconds,
        output_dir=DEFAULT_QLIB_STYLE_OUTPUT_DIR if args.qlib_style and args.output_dir == DEFAULT_OUTPUT_DIR else args.output_dir,
        file_format=args.format,
        append=not args.no_append,
        qlib_style=args.qlib_style,
        log_level=args.log_level,
        skip_empty=not args.keep_empty,
    )

    _configure_logging(config.log_level)
    LOGGER.info(
        "download config period=%s adjust=%s start=%s end=%s qlib_style=%s append=%s output_dir=%s",
        config.period,
        config.adjust or "none",
        config.start_date or "none",
        config.end_date or "none",
        config.qlib_style,
        config.append,
        config.output_dir,
    )
    if pool_file:
        LOGGER.info("using stock pool file %s", pool_file)
    elif symbols:
        LOGGER.info("using explicit symbols count=%d", len(symbols))

    saved_paths = download_symbols(symbols, config=config)
    for symbol, path in saved_paths.items():
        print(f"{symbol}\t{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
