# -*- coding: utf-8 -*-
"""Consolidation: what an extracted finding does to the records already kept.

The model proposes duplicates, supersedes, contradicts or a link; the store decides,
and every test checks what ended up stored.
"""
from __future__ import annotations

import json

from kernel.memory.extractor import process_session
from kernel.memory.store import MemoryStore
from kernel.tests.test_memory import _SESSION, FakeModel, _journal, _remember, _turn


def _reply(*candidates):
    return json.dumps({"candidates": list(candidates), "recap": None})


def _tool_turn(request, answer, summary="tests passed"):
    return _turn(request, answer, {"type": "action_result", "action_type": "run_shell", "ok": True, "exit_code": 0,
                                   "summary": summary})


def test_a_duplicate_adds_a_source_instead_of_a_second_record(tmp_path):
    store = MemoryStore.for_workspace(tmp_path)
    known = _remember(store, "The e2e tests need a running local postgres")
    _journal(tmp_path, [_turn("why do e2e fail?", "Postgres was down again; e2e need it running.")])
    model = FakeModel(_reply({"kind": "project_fact", "content": "End-to-end tests require PostgreSQL to be up",
                              "source_event_ids": ["e2"],
                              "relation": {"type": "duplicates", "to": known.id}}))

    summary = process_session(store, _SESSION, model.access())

    assert summary["merged"] == [known.id] and summary["accepted"] == []
    assert store.counts()["total"] == 1
    assert store.get(known.id).content == "The e2e tests need a running local postgres"
    assert [source["session"] for source in store.explain(known.id)["sources"]][-1] == _SESSION
    assert known.id in model.asked[0][1]["content"]


def test_a_replacement_shown_by_a_tool_supersedes_the_old_record(tmp_path):
    store = MemoryStore.for_workspace(tmp_path)
    old = _remember(store, "Config writes are guarded by a global asyncio.Lock")
    _journal(tmp_path, [_tool_turn("replace the config lock with a queue", "Done: a queue serializes writes.")])
    model = FakeModel(_reply({"kind": "decision", "content": "Config writes go through a queue; the global lock "
                              "was removed because it deadlocked", "source_event_ids": ["e2", "e3"],
                              "relation": {"type": "supersedes", "to": old.id, "reason": "the lock was removed"}}))

    summary = process_session(store, _SESSION, model.access())
    new_id = summary["accepted"][0]

    assert store.get(old.id).status == "superseded" and store.get(old.id).superseded_by == new_id
    assert summary["links"][0]["result"] == "linked"


def test_a_replacement_only_claimed_in_words_becomes_a_conflict(tmp_path):
    store = MemoryStore.for_workspace(tmp_path)
    old = _remember(store, "Config writes are guarded by a global asyncio.Lock")
    _journal(tmp_path, [_turn("do we still use the lock?", "I think it was replaced by a queue.")])
    model = FakeModel(_reply({"kind": "project_fact", "content": "Config writes may use a queue now",
                              "source_event_ids": ["e2"],
                              "relation": {"type": "supersedes", "to": old.id, "reason": "the answer says so"}}))

    summary = process_session(store, _SESSION, model.access())
    new_id = summary["accepted"][0]

    assert store.get(old.id).status == "conflicted" and store.get(new_id).status == "conflicted"
    assert summary["links"][0]["relation"] == "contradicts"


def test_the_persons_decision_is_never_replaced_by_extraction(tmp_path):
    store = MemoryStore.for_workspace(tmp_path)
    decision = _remember(store, "Releases are cut on Fridays", kind="decision", trust="person", actor="person")
    _journal(tmp_path, [_tool_turn("when does the release job run?", "The cron now runs it on Mondays.",
                                   "crontab shows monday")])
    # e2 is the tool result, so only the person-confirmed target keeps this from being a replacement.
    model = FakeModel(_reply({"kind": "project_fact", "content": "The release job runs on Mondays",
                              "source_event_ids": ["e2"],
                              "relation": {"type": "supersedes", "to": decision.id, "reason": "cron says Monday"}}))

    summary = process_session(store, _SESSION, model.access())

    assert summary["links"][0]["result"] == "conflict"
    assert store.get(decision.id).status == "conflicted"
    assert store.get(decision.id).content == "Releases are cut on Fridays"


def test_a_link_to_an_id_the_model_was_not_shown_is_refused(tmp_path):
    store = MemoryStore.for_workspace(tmp_path)
    hidden = _remember(store, "Completely unrelated: the logo is purple")
    _journal(tmp_path, [_turn("why do e2e fail?", "They need postgres.")])
    model = FakeModel(_reply({"kind": "project_fact", "content": "The e2e tests need postgres",
                              "source_event_ids": ["e2"],
                              "relation": {"type": "supersedes", "to": hidden.id, "reason": "x"}}))

    summary = process_session(store, _SESSION, model.access())

    assert summary["accepted"] == [] and "neither a record shown" in summary["rejected"][0]["reason"]
    assert store.get(hidden.id).status == "active"


def test_candidates_link_to_each_other_even_after_a_rejection_shifts_them(tmp_path):
    store = MemoryStore.for_workspace(tmp_path)
    _journal(tmp_path, [_tool_turn("the import hangs, fix it",
                                   "It hung because lxml blocks in threads; it now runs in a subprocess.")])
    model = FakeModel(_reply(
        {"kind": "project_fact", "content": "Invented", "source_event_ids": ["e99"]},
        {"kind": "project_fact", "content": "The importer hung because lxml blocks inside threads",
         "source_event_ids": ["e3"], "relation": {"type": "resolved_by", "to": "c2"}},
        {"kind": "decision", "content": "The importer runs lxml in a subprocess, because it blocks inside threads",
         "source_event_ids": ["e2", "e3"]},
    ))

    summary = process_session(store, _SESSION, model.access())
    problem, fix = summary["accepted"]
    relations = store.explain(problem)["relations"]

    assert len(summary["rejected"]) == 1
    assert [(item["relation"], item["target_id"]) for item in relations] == [("resolved_by", fix)]


def test_a_link_to_a_rejected_candidate_is_dropped_not_misdirected(tmp_path):
    store = MemoryStore.for_workspace(tmp_path)
    _journal(tmp_path, [_turn("q", "a")])
    model = FakeModel(_reply(
        {"kind": "project_fact", "content": "Real finding", "source_event_ids": ["e2"],
         "relation": {"type": "related_to", "to": "c1"}},
        {"kind": "project_fact", "content": "Token ghp_abcdefghijklmnopqrstuvwxyz0123", "source_event_ids": ["e2"]},
        {"kind": "project_fact", "content": "Another real finding", "source_event_ids": ["e2"]},
    ))

    summary = process_session(store, _SESSION, model.access())

    assert len(summary["accepted"]) == 2 and summary["links"] == []
    assert store.explain(summary["accepted"][0])["relations"] == []
