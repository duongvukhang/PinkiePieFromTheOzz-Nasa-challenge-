"""Time conversion and FITS epoch parsing utilities (MJD TDB)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import astropy.units as u
from astropy.io import fits
from astropy.time import Time

_VALID_SCALES = frozenset({"utc", "tai", "tt", "tdb", "tcg", "tcb", "ut1"})


@dataclass(frozen=True)
class ParsedEpoch:
    """Normalized mid-exposure epoch (MJD TDB, UTC ISO string, exposure s)."""

    mjd_tdb_mid: float
    utc_iso: str
    exptime_s: float | None
    timesys: str
    user_supplied: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def t_mid(self) -> Time:
        """Mid-exposure epoch as an Astropy `Time` in TDB scale (days)."""
        return Time(self.mjd_tdb_mid, format="mjd", scale="tdb")


def _parse_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _parse_time_value(value: Any, *, is_mjd: bool, scale: str) -> Time | None:
    if value is None:
        return None
    if is_mjd:
        num = _parse_float(value)
        return Time(num, format="mjd", scale=scale) if num is not None else None
    text = str(value).strip()
    if not text:
        return None
    return Time(text, scale=scale)


def parse_user_epoch(value: Time | str | float | int) -> Time:
    """Parse an explicit epoch override into an Astropy `Time` in TDB scale."""
    if isinstance(value, Time):
        return value.tdb
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return Time(float(value), format="mjd", scale="tdb").tdb
    text = str(value).strip()
    if text.endswith("+00:00") or text.endswith("-00:00"):
        text = text[:-6]
    elif text.endswith("Z") or text.endswith("z"):
        text = text[:-1]
    return Time(text, scale="utc").tdb


def parse_fits_epoch(
    headers: fits.Header | list[fits.Header],
    *,
    user_epoch: Time | str | float | int | None = None,
) -> tuple[ParsedEpoch | None, list[str]]:
    """Extract mid-exposure epoch (MJD TDB) and exposure duration (s) from FITS headers."""
    hdr_list = [headers] if isinstance(headers, fits.Header) else list(headers)
    warnings: list[str] = []

    def _first_val(keys: tuple[str, ...]) -> tuple[str | None, Any]:
        for hdr in hdr_list:
            for key in keys:
                if key in hdr and hdr[key] not in (None, ""):
                    return key, hdr[key]
        return None, None

    _, raw_sys = _first_val(("TIMESYS",))
    timesys = str(raw_sys).strip().lower() if raw_sys is not None else "utc"
    if not timesys or timesys == "ut":
        timesys = "utc"
    elif timesys not in _VALID_SCALES:
        warnings.append(f"Unrecognized TIMESYS {raw_sys!r}; assuming 'utc'.")
        timesys = "utc"

    exp_key, exp_raw = _first_val(("EXPTIME", "XPOSURE", "TELAPSE"))
    exptime_s = _parse_float(exp_raw)
    if exp_raw is not None and (exptime_s is None or exptime_s < 0.0):
        warnings.append(f"Invalid exposure keyword {exp_key}={exp_raw!r}; ignoring.")
        exptime_s = None

    def _first_time(pairs: tuple[tuple[str, bool], ...]) -> tuple[str | None, Time | None]:
        for key, is_mjd in pairs:
            _, val = _first_val((key,))
            if val is None:
                continue
            try:
                parsed = _parse_time_value(val, is_mjd=is_mjd, scale=timesys)
            except Exception:
                warnings.append(f"Unparseable time keyword {key}={val!r}; skipping.")
                continue
            if parsed is not None:
                return key, parsed
        return None, None

    t_mid: Time | None = None
    _, t_avg = _first_time((("MJD-AVG", True), ("DATE-AVG", False)))
    if t_avg is not None:
        t_mid = t_avg
    else:
        _, t_beg = _first_time((("DATE-BEG", False), ("MJD-BEG", True)))
        _, t_end = _first_time((("DATE-END", False), ("MJD-END", True)))
        if t_beg is not None and t_end is not None and t_end >= t_beg:
            t_mid = t_beg + 0.5 * (t_end - t_beg)
            if exptime_s is None:
                exptime_s = float((t_end - t_beg).to_value(u.s))
        elif t_beg is not None:
            t_mid = t_beg + (0.5 * exptime_s * u.s if exptime_s else 0.0 * u.s)
        else:
            obs_key, t_obs = _first_time((("DATE-OBS", False), ("MJD-OBS", True)))
            if t_obs is not None:
                if exptime_s is not None and exptime_s > 0.0:
                    t_mid = t_obs + 0.5 * exptime_s * u.s
                else:
                    t_mid = t_obs
                    if exptime_s is None:
                        warnings.append(
                            f"EXPTIME missing alongside {obs_key}; using header epoch directly "
                            "without 0.5*EXPTIME mid-exposure shift."
                        )

    user_supplied = False
    if t_mid is None and user_epoch is not None:
        try:
            t_mid = parse_user_epoch(user_epoch)
            user_supplied = True
        except Exception as exc:
            warnings.append(f"Invalid user_epoch override {user_epoch!r}: {exc}")

    if t_mid is None:
        return None, warnings

    t_tdb = t_mid.tdb
    utc_iso = str(t_tdb.utc.isot) + "Z"
    return (
        ParsedEpoch(
            mjd_tdb_mid=float(t_tdb.mjd),
            utc_iso=utc_iso,
            exptime_s=exptime_s,
            timesys=timesys.upper(),
            user_supplied=user_supplied,
            warnings=warnings,
        ),
        warnings,
    )


__all__ = ["ParsedEpoch", "parse_fits_epoch", "parse_user_epoch"]
