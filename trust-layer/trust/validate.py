"""Strikte invoervalidatie: onbekende velden worden geweigerd, elk veld heeft een type en een limiet."""
import re
from datetime import date

ID_RE = re.compile(r"^[A-Za-z0-9._#-]{1,200}$")
FILENAME_RE = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]{1,200}$")


class Invalid(ValueError):
    pass


def text(maxlen, required=False, pattern=None):
    def check(name, v):
        if v is None or v == "":
            if required:
                raise Invalid(f"{name} is required")
            return None
        if not isinstance(v, str):
            raise Invalid(f"{name} must be text")
        v = v.strip()
        if len(v) > maxlen:
            raise Invalid(f"{name} is too long (max. {maxlen} chars)")
        if pattern and not pattern.match(v):
            raise Invalid(f"{name} has an invalid format")
        if required and not v:
            raise Invalid(f"{name} is required")
        return v or None
    return check


def ident(required=True):
    return text(200, required, ID_RE)


def enum(*options, required=False):
    def check(name, v):
        if v in (None, ""):
            if required:
                raise Invalid(f"{name} is required")
            return None
        if v not in options:
            raise Invalid(f"{name} must be one of {', '.join(options)}")
        return v
    return check


def iso_date(required=False):
    def check(name, v):
        if v in (None, ""):
            if required:
                raise Invalid(f"{name} is required")
            return None
        if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise Invalid(f"{name} must be a date (YYYY-MM-DD)")
        try:
            d = date.fromisoformat(v)
        except ValueError:
            raise Invalid(f"{name} is not a valid date")
        if not 2000 <= d.year <= 2100:
            raise Invalid(f"{name} is out of range")
        return v
    return check


def boolean():
    def check(name, v):
        if v in (None, False, True):
            return bool(v)
        raise Invalid(f"{name} must be true or false")
    return check


def upload(max_b64):
    def check(name, v):
        if v is None:
            return None
        if not isinstance(v, dict) or set(v) - {"name", "data"}:
            raise Invalid(f"{name} has an invalid structure")
        fname = text(200, True, FILENAME_RE)(f"{name}.name", v.get("name"))
        data = v.get("data")
        if not isinstance(data, str) or not data or len(data) > max_b64:
            raise Invalid(f"{name} is empty or too large")
        return {"name": fname, "data": data}
    return check


def body(payload, spec):
    if not isinstance(payload, dict):
        raise Invalid("request body must be a JSON object")
    extra = set(payload) - set(spec)
    if extra:
        raise Invalid("unexpected fields: " + ", ".join(sorted(extra)))
    return {k: check(k, payload.get(k)) for k, check in spec.items()}


def query(qs, spec):
    extra = set(qs) - set(spec)
    if extra:
        raise Invalid("unexpected parameters: " + ", ".join(sorted(extra)))
    return {k: check(k, (qs.get(k) or [None])[0]) for k, check in spec.items()}
