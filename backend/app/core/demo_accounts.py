"""The pre-provisioned demonstration accounts.

These are the synthetic accounts created by seed.py, in the canonical
+91XXXXXXXXXX form used for every stored phone number. They are the only
numbers that AUTH_DEMO_OTP_NUMBERS may list (Settings refuses any other),
so reviewer access can never reach a real or newly created account.
tests/test_demo_otp.py checks that each one exists in the seed with the
expected role.
"""

DEMO_RETAILER_NUMBERS = ("+919000000001", "+919000000002", "+919000000003")
DEMO_ORDER_DESK_NUMBER = "+919000000010"

DEMO_ACCOUNT_NUMBERS = frozenset((*DEMO_RETAILER_NUMBERS, DEMO_ORDER_DESK_NUMBER))
