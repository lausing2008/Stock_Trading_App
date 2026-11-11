"""AUD-SQZ-SAMEDAY-REPEAT (2026-11-10): a squeeze symbol is emailed at most once per recipient
per ET day per alert, however often it drops out of and back into qualification."""
import importlib

sched = importlib.import_module("src.services.scheduler")


class _Redis:
    def __init__(self):
        self.sets = {}

    def smembers(self, k):
        return set(self.sets.get(k, set()))

    def sadd(self, k, *v):
        self.sets.setdefault(k, set()).update(v)

    def expire(self, k, ttl):
        pass


def _cycle(rc, prev_active, current, prefix="squeeze", uid=1):
    """One run of the job's per-recipient logic, as wired in the scheduler."""
    newly = sorted(set(current) - set(prev_active))
    newly = sched._drop_sent_today(rc, prefix, uid, newly)
    if newly:
        sched._mark_sent_today(rc, prefix, uid, newly)
    return newly, set(current)


def test_a_symbol_hovering_at_the_threshold_emails_once_a_day():
    rc, active, sends = _Redis(), set(), []
    for current in (["XYZ"], [], ["XYZ"], [], ["XYZ"]):     # 3.1%, 2.9%, 3.2%, ...
        newly, active = _cycle(rc, active, current)
        sends.append(newly)
    assert sends == [["XYZ"], [], [], [], []]


def test_a_different_symbol_still_alerts_the_same_day():
    rc, active = _Redis(), set()
    _, active = _cycle(rc, active, ["XYZ"])
    newly, _ = _cycle(rc, active, ["XYZ", "ABC"])
    assert newly == ["ABC"]


def test_the_two_alert_types_are_independent():
    rc = _Redis()
    assert _cycle(rc, set(), ["XYZ"], prefix="squeeze")[0] == ["XYZ"]
    assert _cycle(rc, set(), ["XYZ"], prefix="squeeze_ignition")[0] == ["XYZ"]


def test_the_record_is_per_recipient_and_per_et_day():
    assert sched._sent_today_key("squeeze", 1).endswith(":" + sched._today_et().isoformat())
    assert sched._sent_today_key("squeeze", 1) != sched._sent_today_key("squeeze", 2)


def test_a_redis_failure_keeps_every_symbol():
    class Broken:
        def smembers(self, k):
            raise RuntimeError("down")
    assert sched._drop_sent_today(Broken(), "squeeze", 1, ["XYZ"]) == ["XYZ"]


def test_both_jobs_are_wired():
    src = open(sched.__file__).read()
    for job, prefix in (("check_short_squeeze_alerts", "squeeze"), ("check_squeeze_ignition_alerts", "squeeze_ignition")):
        body = src[src.index(f"def {job}("):]
        body = body[:body.index("\ndef ")]
        assert f'_drop_sent_today(_rc, "{prefix}", uid, newly_qualifying)' in body
        assert f'_mark_sent_today(_rc, "{prefix}", uid, newly_qualifying)' in body
