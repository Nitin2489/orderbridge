"""Phone number validation and normalisation.

OrderBridge serves retailers in India, and the seeded accounts use E.164
Indian mobile numbers (+91 followed by 10 digits). Every phone number is
normalised to that form before it is stored or compared.
"""

import re

COUNTRY_PREFIX = "+91"

_SEPARATORS = re.compile(r"[\s\-().]")
_INDIAN_MOBILE = re.compile(r"[6-9][0-9]{9}")


class InvalidPhoneNumberError(ValueError):
    """The value is not a valid Indian mobile number."""


def normalize_phone_number(raw: str) -> str:
    """Return the number as +91XXXXXXXXXX.

    Accepts +91XXXXXXXXXX, 91XXXXXXXXXX, 0XXXXXXXXXX or XXXXXXXXXX, with
    optional spaces, hyphens, dots or brackets. The 10-digit national number
    must start with 6-9, as Indian mobile numbers do.
    """
    compact = _SEPARATORS.sub("", raw.strip())

    if compact.startswith(COUNTRY_PREFIX):
        national = compact[len(COUNTRY_PREFIX) :]
    elif compact.startswith("91") and len(compact) == 12:
        national = compact[2:]
    elif compact.startswith("0") and len(compact) == 11:
        national = compact[1:]
    else:
        national = compact

    if not _INDIAN_MOBILE.fullmatch(national):
        raise InvalidPhoneNumberError("Enter a valid 10-digit Indian mobile number.")

    return f"{COUNTRY_PREFIX}{national}"
