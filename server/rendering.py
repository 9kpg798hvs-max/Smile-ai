"""Template rendering with the supported placeholder set.

Unknown placeholders are left visible ({like_this}) rather than erased, so a
typo in a template is caught at preview, never silently sent as blank text.
"""

import re

PLACEHOLDERS = {
    "first_name",
    "doctor_name",
    "office_name",
    "procedure",
    "treatment_date",
    "office_phone",
}

_TOKEN = re.compile(r"\{([a-z_]+)\}")


def render_template(body: str, context: dict) -> str:
    def sub(match: re.Match) -> str:
        key = match.group(1)
        if key in PLACEHOLDERS and context.get(key):
            return str(context[key])
        return match.group(0)  # leave unknown/empty visible

    return _TOKEN.sub(sub, body)
