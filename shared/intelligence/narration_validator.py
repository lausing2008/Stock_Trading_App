"""REMOVED: there is no ungated free-prose path any more. Use `claims.narrate`.

WHY THE WHOLE ENTRY POINT WENT. A review reproduced two bypasses here and the fix for them is
not a longer word list. `validate(text, packet)` had no CLAIM to check the text against, so it
could only ask "does this text contain a forbidden phrase" — and "profits surpassed analyst
forecasts" is not on any list that stays maintainable, while an attribution in one sentence
disarmed the causal guard for every other. Both are properties of grading undifferentiated
prose, not of the particular phrases chosen.

A claim now arrives with its kind, its quantities and its own comment, and the comment is
checked against THAT claim's permissions. There is no mode in which text is graded with nothing
to grade it against, so neither bypass has anywhere to live.

The checks that were worth keeping — price window, historical framing, conflict presented as
settled, asserting a field the packet reports missing — moved into `claims._scoped_packet_checks`
and now run per claim.
"""

raise ImportError(
    "narration_validator.validate has been removed: free prose was never checkable without a "
    "claim to check it against. Use intelligence.claims.narrate(claims, packet, "
    "deterministic=...) — the narrator names quantities and the figures are rendered from the "
    "packet. See docs/audits/2026-10-04-n1-packet-validation-review.md findings N1-R01/R03.")
