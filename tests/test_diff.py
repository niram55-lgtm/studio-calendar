"""Tests for compute_diff."""
from __future__ import annotations

import datetime as dt

from calendar_google import compute_diff, SyncDiff
from calendar_ics import loose_uid, strict_uid
from models import ClassSnapshot
from scraper import ClassItem


def _c(date=dt.date(2026, 2, 2), time=dt.time(18, 0),
       trainer="שירן", class_type="Body Pump", room="POWER") -> ClassItem:
    return ClassItem(date=date, time=time, trainer=trainer,
                     class_type=class_type, room=room, source="live")


def _snap_from(c: ClassItem) -> ClassSnapshot:
    return ClassSnapshot(
        user_id=1,
        loose_uid=loose_uid(c),
        strict_uid=strict_uid(c),
        class_date=c.date,
        class_time=c.time.strftime("%H:%M"),
        trainer=c.trainer,
        class_type=c.class_type,
        room=c.room,
    )


def test_diff_added():
    diff = compute_diff(old=[], new=[_c(), _c(time=dt.time(19, 0),
                                         class_type="Pilates")])
    assert len(diff.added) == 2
    assert diff.removed == []
    assert diff.changed == []


def test_diff_removed():
    old = [_snap_from(_c()), _snap_from(_c(class_type="Pilates"))]
    diff = compute_diff(old=old, new=[])
    assert diff.removed == old
    assert diff.added == []
    assert diff.changed == []


def test_diff_time_change_is_changed_not_removed_and_added():
    old_c = _c(time=dt.time(18, 0))
    new_c = _c(time=dt.time(19, 0))
    diff = compute_diff(old=[_snap_from(old_c)], new=[new_c])
    assert diff.added == []
    assert diff.removed == []
    assert len(diff.changed) == 1
    old_snap, new_item = diff.changed[0]
    assert old_snap.class_time == "18:00"
    assert new_item.time == dt.time(19, 0)


def test_diff_room_change_is_changed():
    old_c = _c(room="POWER")
    new_c = _c(room="MOTION")
    diff = compute_diff(old=[_snap_from(old_c)], new=[new_c])
    assert len(diff.changed) == 1


def test_diff_trainer_change_is_removed_plus_added():
    old_c = _c(trainer="שירן")
    new_c = _c(trainer="אור")
    diff = compute_diff(old=[_snap_from(old_c)], new=[new_c])
    assert len(diff.removed) == 1
    assert len(diff.added) == 1
    assert diff.changed == []


def test_diff_unchanged_item_not_in_any_bucket():
    c = _c()
    diff = compute_diff(old=[_snap_from(c)], new=[c])
    assert diff.added == [] and diff.removed == [] and diff.changed == []


def test_same_class_twice_in_day_both_tracked():
    """Review fix #4: two Pilates slots on the same day — morning and
    evening — must both appear, not collapse to one."""
    morning = _c(time=dt.time(8, 0), class_type="Pilates")
    evening = _c(time=dt.time(18, 0), class_type="Pilates")
    assert loose_uid(morning) == loose_uid(evening)  # same loose key
    # First sync: both are added.
    diff = compute_diff(old=[], new=[morning, evening])
    assert len(diff.added) == 2
    # Second sync: both already snapshotted → nothing to do.
    snaps = [_snap_from(morning), _snap_from(evening)]
    diff = compute_diff(old=snaps, new=[morning, evening])
    assert diff.added == [] and diff.removed == [] and diff.changed == []
    # Cancel morning: it should be reported as removed, evening untouched.
    diff = compute_diff(old=snaps, new=[evening])
    assert len(diff.removed) == 1
    assert diff.removed[0].strict_uid == strict_uid(morning)
    assert diff.added == []
