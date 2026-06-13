"""Task #17b-4: the recorder wired onto resolve_identity. Settled identity routes through the
ONE gate, structure-twins (same-skeleton sibling pages) split into distinct nodes on an OBSERVED
collision, and every holder of a rekeyed id (the source local, current_id, typing tags,
provisional strings, a frozen anchor) is repaired in the same pass — so no edge references a
vanished node and no typed value is dropped (the lifecycle blockers the adversarial review found)."""
from wendle.capture.types import Gesture
from wendle.driver.fake import FakeDriver
from wendle.fingerprint.signature import fingerprint, refined_id
from wendle.models import Action, DeviceProfile, Selector
from wendle.record.session import RecordSession

PROFILE = DeviceProfile(touchscreen_node="/dev/input/event3", abs_x=(0, 1079), abs_y=(0, 2339),
                        display=(1080, 2340), touch_protocol="type_b")
NOSLEEP = {"sleep": lambda _dt: None}
NS = "com.app/.SubSettings"
CFG_FOCUS = "com.app"


def _dumpsys(ns):
    pkg, _, act = ns.partition("/")
    return (f"topResumedActivity: ActivityRecord{{x u0 {pkg}/{act} t1}}",
            f"mCurrentFocus=Window{{x u0 {pkg}/{act}}}")


def _twin(title):
    # a same-skeleton SubSettings page: a desc-bearing toolbar title (desc-PRESENCE is structural
    # so all twins share a structure_id) + a clickable list row. The title VALUE distinguishes
    # the chrome digest, so two titles collide structurally and split on the digest.
    title = title.replace("&", "&amp;")
    return ('<hierarchy><node class="android.widget.FrameLayout" package="com.app" resource-id="" '
            'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
            f'<node class="android.widget.TextView" package="com.app" resource-id="com.app:id/title" '
            f'clickable="false" content-desc="{title}" text="" bounds="[40,40][800,160]"/>'
            '<node class="android.widget.TextView" package="com.app" resource-id="com.app:id/row" '
            'clickable="true" content-desc="" text="Open" bounds="[0,400][1080,520]"/>'
            "</node></hierarchy>")


def _home():
    return ('<hierarchy><node class="android.widget.FrameLayout" package="com.app" resource-id="" '
            'clickable="false" content-desc="" text="" bounds="[0,0][1080,2340]">'
            '<node class="android.widget.Button" package="com.app" resource-id="com.app:id/enter" '
            'clickable="true" content-desc="" text="Enter" bounds="[40,500][1040,620]"/></node></hierarchy>')


def _tap(y=450):
    return Gesture(kind="tap", t_down=1.0, t_up=1.05, x=540, y=y)


def _driver(*frames):
    """frames: list of (xml, ns). Each settles via 3 identical dumps."""
    hs, ds = [], []
    for xml, ns in frames:
        hs += [xml] * 3
        ds += [_dumpsys(ns)] * 3
    return FakeDriver(hierarchies=hs, dumpsys_pairs=ds, display=(1080, 2340))


def _coarse_F():
    return fingerprint(NS, _twin("Network"), None, CFG_FOCUS)


def test_enter_mints_coarse_node_carrying_its_chrome_digest():
    # T2: _enter (the sole minter) stamps chrome_digest on the coarse node AND keeps the rich
    # presentation fields (proves the gate didn't mint a minimal node that drops them).
    drv = _driver((_twin("Network"), NS))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    home = s.start()
    node = s.graph.screen(s.current_id)
    assert node.chrome_digest is not None and node.coarse_id is None
    assert node.profile_name and node.screen_type and node.fingerprint_confidence == "high"


def test_sibling_collision_splits_into_distinct_nodes_with_a_rekey_event():
    # T3: visit Network (coarse F), then a settled Connected-devices (same skeleton, diff title)
    # -> OBSERVED collision -> split. F becomes refined T_old, Connected mints T_new, a rekey
    # event fires, both carry rich scalars.
    events = []
    drv = _driver((_home(), "com.app/.Home"), (_twin("Network"), NS), (_twin("Connected devices"), NS))
    s = RecordSession(drv, PROFILE, sink=events.append, settle_kwargs=NOSLEEP)
    s.start()
    s.record_gesture(_tap(560))   # Home -> Network (mints coarse F)
    F = _coarse_F()
    assert s.graph.screen(F) is not None and s.current_id == F
    s.record_gesture(_tap())      # Network -> Connected: SPLIT
    assert F not in s.graph.g.nodes                       # coarse node rekeyed away
    assert any(e.get("event_type") == "rekey" for e in events)
    twins = [n for n in s.graph.g.nodes if s.graph.screen(n).coarse_id == F]
    assert len(twins) == 2 and all(s.graph.screen(t).fingerprint_confidence == "high" for t in twins)


def _digest_of(s, title):
    from wendle.fingerprint.signature import chrome_digest
    return chrome_digest(_twin(title), None, CFG_FOCUS)


def test_split_while_source_no_crash_edge_uses_refined_source():
    # T4 (THE load-bearing case): from Network (current=coarse F) a tap lands on the sibling
    # Connected-devices -> _enter SPLITS F->T_old, so the local `source` (= F, read before
    # _enter) is stale. The recorder must re-read it: NO crash, and the recorded edge's source
    # is T_old (the refined Network), not the vanished F.
    drv = _driver((_home(), "com.app/.Home"), (_twin("Network"), NS), (_twin("Connected devices"), NS))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    s.record_gesture(_tap(560))   # Home -> Network
    F = _coarse_F()
    T_old = refined_id(F, _digest_of(s, "Network"))
    T_new = refined_id(F, _digest_of(s, "Connected devices"))
    t = s.record_gesture(_tap())  # Network -> Connected: split-while-source
    assert t is not None                          # no AttributeError on the vanished F
    assert t.source == T_old                       # edge bound to the refined source, not F
    assert s.graph.g.has_edge(T_old, T_new)        # the real edge exists
    assert s.current_id == T_new                   # we're on the new twin
    assert F not in s.graph.g.nodes
    assert s.graph.screen(T_old).actions           # the source's action was recorded on T_old


def test_credential_survives_a_split():
    # T5 (Invariant #4): a set_text staged on coarse F must NOT be dropped when F splits to
    # T_old — _remap_id retags the pending F->T_old so the submit on T_old still carries it.
    drv = _driver((_home(), "com.app/.Home"), (_twin("Network"), NS), (_twin("Connected devices"), NS))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    s.record_gesture(_tap(560))   # Home -> Network (current = F)
    F = _coarse_F()
    # stage a pending set_text tagged with the coarse F (as the typing FSM would)
    s._pending.append((F, NS, Action(selector=Selector("resource_id", "com.app:id/pw"),
                                     action_type="set_text", value={"param": "pw"}, sensitive=True)))
    T_old = refined_id(F, _digest_of(s, "Network"))
    t = s.record_gesture(_tap())  # split F->T_old; the submit tap drains the pending onto its edge
    # the credential was NOT dropped: _remap_id retagged it F->T_old, so _take_pending(T_old)
    # found it and it rides the Network->Connected edge as a pre_action.
    assert t.source == T_old
    assert [a.selector.value for a in t.pre_actions] == ["com.app:id/pw"]
    assert s._pending == []  # fully drained, nothing orphaned


def test_start_and_volatile_first_screen_unaffected_by_the_dec_return():
    # T9: start() ignores the new _enter return; settled and volatile first screens still set
    # current_id correctly (no source held -> no split-while-source path).
    drv = _driver((_twin("Network"), NS))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    assert s.current_id == _coarse_F() and s.current_snapshot is not None


def _fresh_dict(xml, ns, stable=2):
    from wendle.capture.hierarchy import parse_hierarchy
    from wendle.fingerprint.compose import resolve_profile
    from wendle.fingerprint.signature import fingerprint as fp, structure_id
    from wendle.models import Action  # noqa
    from wendle.capture.types import Snapshot
    cfg = resolve_profile(xml, ns)
    return {"ns": ns, "snap": Snapshot(0.0, 0.0, "", parse_hierarchy(xml, focus_pkg=ns.split('/')[0])),
            "id": fp(ns, xml, cfg, focus_pkg=ns.split('/')[0]),
            "struct": structure_id(ns, xml, focus_pkg=ns.split('/')[0]),
            "profile_name": "view", "focus": ns.split('/')[0], "stable": stable,
            "xml": xml, "cfg": cfg}


def _refined_family_session():
    """A session whose graph already holds a refined family {T_net, T_conn} (coarse F gone),
    with current_id on an unrelated screen X."""
    drv = _driver((_home(), "com.app/.Home"), (_twin("Network"), NS), (_twin("Connected devices"), NS))
    s = RecordSession(drv, PROFILE, settle_kwargs=NOSLEEP)
    s.start()
    s.record_gesture(_tap(560))   # Home -> Network (coarse F)
    s.record_gesture(_tap())      # Network -> Connected: SPLIT
    F = _coarse_F()
    return s, F


def test_reconcile_resolves_to_existing_twin_never_resurrects_coarse():
    # T8: a dropped navigation lands the device on the (already-refined) Network sibling while
    # current_id is elsewhere. Reconcile must resolve to the EXISTING twin T_net, NEVER mint a
    # fresh coarse node F beside the refined family (the F4 identity fork).
    s, F = _refined_family_session()
    T_net = refined_id(F, _digest_of(s, "Network"))
    # park current_id on an unrelated screen X (different structure), then the refresher catches
    # the (already-refined) Network sibling — a dropped navigation onto a family member.
    from wendle.models import Screen
    s.graph.upsert_screen(Screen(id="X-unknown", namespace="com.app/.Home", structure_id="Sx"))
    s.current_id = "X-unknown"
    s.current_snapshot = _fresh_dict(_home(), "com.app/.Home")["snap"]
    s._fresh = _fresh_dict(_twin("Network"), NS)
    s._reconcile_current_screen()
    assert F not in s.graph.g.nodes          # coarse F was NOT resurrected
    assert s.current_id == T_net             # reconciled to the existing twin


def test_reconcile_skips_when_chrome_matches_no_existing_twin():
    # a refresher guess whose chrome matches NO existing twin in a refined family must NOT mint
    # (neither a new twin nor a resurrected coarse F) — it leaves current_id unchanged.
    s, F = _refined_family_session()
    from wendle.models import Screen
    s.graph.upsert_screen(Screen(id="X-unknown", namespace="com.app/.Home", structure_id="Sx"))
    s.current_id = "X-unknown"
    s.current_snapshot = _fresh_dict(_home(), "com.app/.Home")["snap"]
    before = set(s.graph.g.nodes)
    s._fresh = _fresh_dict(_twin("Bluetooth"), NS)  # a THIRD sibling never recorded
    s._reconcile_current_screen()
    assert F not in s.graph.g.nodes                  # no coarse resurrection
    assert set(s.graph.g.nodes) == before            # no new node minted at all
    assert s.current_id == "X-unknown"               # current unchanged (honest: unknown twin)


def test_coarsen_family_mid_record_does_not_crash_and_repairs_source():
    # CRITICAL adversarial finding: when FAMILY_MAX is hit during a live record_gesture, coarsen
    # rekeys every family member into coarse F — but record_gesture only handled `rekeyed` (split),
    # not `coarsened`, so the local `source` went stale -> AttributeError. Now unified via
    # node_remap: no crash, source repaired to F, staged credential not orphaned.
    from wendle.record.identity import FAMILY_MAX
    from wendle.fingerprint.signature import fingerprint, chrome_digest, refined_id, structure_id
    from wendle.models import Screen
    F = _coarse_F()
    struct = structure_id(NS, _twin("Network"), CFG_FOCUS)
    s_drv = _driver((_twin(f"Churn {FAMILY_MAX}"), NS))  # the (FAMILY_MAX+1)th distinct chrome
    s = RecordSession(s_drv, PROFILE, settle_kwargs=NOSLEEP)
    # seed a FULL refined family (FAMILY_MAX distinct twins sharing coarse F)
    member_ids = []
    for i in range(FAMILY_MAX):
        d = chrome_digest(_twin(f"Churn {i}"), None, CFG_FOCUS)
        tid = refined_id(F, d)
        s.graph.upsert_screen(Screen(id=tid, namespace=NS, structure_id=struct, package="com.app",
                                     activity=".SubSettings", profile_name="view",
                                     chrome_digest=d, coarse_id=F))
        member_ids.append(tid)
    # we are ON one member, with a staged credential and current_snapshot
    s.current_id = member_ids[10]
    from wendle.capture.types import Snapshot
    from wendle.capture.hierarchy import parse_hierarchy
    s.current_snapshot = Snapshot(0.0, 0.0, "", parse_hierarchy(_twin("Churn 10")))
    s._pending.append((member_ids[10], NS, Action(selector=Selector("resource_id", "com.app:id/pw"),
                                                  action_type="set_text", value={"param": "pw"})))
    # a tap that settles a NEW chrome -> family exceeds FAMILY_MAX -> coarsen
    t = s.record_gesture(_tap())
    assert F in s.graph.g.nodes                       # coarsened back to the single coarse node
    assert s.graph.is_twin_exhausted(F)               # and blacklisted
    assert s.current_id == F                           # current repaired off the vanished member
    # the credential was retagged to F, not orphaned on a dead member id
    assert all(sid != member_ids[10] for (sid, _n, _a) in s._pending) or s._pending == []


def test_resume_applies_a_split_rename_so_a_staged_credential_is_not_dropped():
    # HIGH adversarial finding: resume() discarded the _enter dec; a split during the re-anchor
    # left a pre-pause credential tagged with the vanished coarse F -> silently dropped.
    from wendle.fingerprint.signature import chrome_digest, fingerprint, refined_id, structure_id
    from wendle.models import Screen
    F = _coarse_F()
    # a pre-existing coarse-F node (Network) carrying its digest, ready to split on a sibling visit
    dN = chrome_digest(_twin("Network"), None, CFG_FOCUS)
    s_drv = _driver((_twin("Connected devices"), NS))  # resume re-anchors onto the sibling -> split
    s = RecordSession(s_drv, PROFILE, settle_kwargs=NOSLEEP)
    s.graph.upsert_screen(Screen(id=F, namespace=NS, structure_id=structure_id(NS, _twin("Network"), CFG_FOCUS),
                                 package="com.app", activity=".SubSettings", profile_name="view",
                                 chrome_digest=dN))
    s.current_id = F
    s._pending.append((F, NS, Action(selector=Selector("resource_id", "com.app:id/pw"),
                                     action_type="set_text", value={"param": "pw"}, sensitive=True)))
    s.paused = True
    s.resume()
    T_net = refined_id(F, dN)
    assert F not in s.graph.g.nodes                    # F split into T_net (+ the Connected twin)
    # the credential followed F -> T_net; it is NOT orphaned on the dead F id
    assert any(sid == T_net for (sid, _n, _a) in s._pending)


def test_human_merge_of_two_twins_blacklists_the_family_no_refork():
    # HIGH adversarial finding: mark_same/merge_screens on two refined twins left the survivor's
    # coarse_id/chrome_digest set and never blacklisted the family, so the very next settled visit
    # to the merged-away page re-MINTED it as a distinct twin — silently undoing the human's merge.
    # The human's "these are one screen" must STICK: blacklist the family + clear the survivor.
    from wendle.fingerprint.signature import chrome_digest, structure_id
    from wendle.models import Screen
    from wendle.record.identity import resolve_identity
    from wendle.fingerprint.compose import resolve_profile
    F = _coarse_F()
    struct = structure_id(NS, _twin("Network"), CFG_FOCUS)
    s = RecordSession(_driver((_twin("Network"), NS)), PROFILE, settle_kwargs=NOSLEEP)
    dN, dC = chrome_digest(_twin("Network"), None, CFG_FOCUS), chrome_digest(_twin("Connected"), None, CFG_FOCUS)
    from wendle.fingerprint.signature import refined_id
    TN, TC = refined_id(F, dN), refined_id(F, dC)
    for tid, d in ((TN, dN), (TC, dC)):
        s.graph.upsert_screen(Screen(id=tid, namespace=NS, structure_id=struct, package="com.app",
                                     activity=".SubSettings", profile_name="view", chrome_digest=d, coarse_id=F))
    s.mark_same(TN, TC)                       # human: "Network and Connected are the SAME screen"
    assert s.graph.is_twin_exhausted(F)       # the family is blacklisted
    assert TN not in s.graph.g.nodes and TC not in s.graph.g.nodes  # family coarsened back to F
    assert s.graph.screen(F).coarse_id is None and s.graph.screen(F).chrome_digest is None
    # the next settled visit to the (merged-away) page does NOT re-mint a distinct twin
    cfg = resolve_profile(_twin("Connected"), NS)
    dec = resolve_identity(s.graph, NS, _twin("Connected"), CFG_FOCUS, True, cfg)
    assert dec.node_remap is None and dec.coarse_id is None  # stays coarse, no re-fork


def _seed_full_family(s, F, struct, n):
    from wendle.fingerprint.signature import chrome_digest, refined_id
    from wendle.models import Screen
    ids = []
    for i in range(n):
        d = chrome_digest(_twin(f"Churn {i}"), None, CFG_FOCUS)
        tid = refined_id(F, d)
        s.graph.upsert_screen(Screen(id=tid, namespace=NS, structure_id=struct, package="com.app",
                                     activity=".SubSettings", profile_name="view",
                                     chrome_digest=d, coarse_id=F))
        ids.append(tid)
    return ids


def test_coarsen_remaps_a_provisional_inter_member_edge_to_a_live_edge():
    # HIGH re-verification finding: coarsen composed N merge remaps via .update(), so a provisional
    # edge between two merged members was rewritten to an INTERMEDIATE (dead) key -> reject/confirm
    # silently no-op'd (an unkillable honesty zombie). The remap must CHAIN-resolve to the live key.
    from wendle.fingerprint.signature import structure_id
    from wendle.models import Action, Selector, Transition
    from wendle.record.identity import FAMILY_MAX
    F = _coarse_F()
    struct = structure_id(NS, _twin("Network"), CFG_FOCUS)
    s = RecordSession(_driver((_twin(f"Churn {FAMILY_MAX}"), NS)), PROFILE, settle_kwargs=NOSLEEP)
    members = _seed_full_family(s, F, struct, FAMILY_MAX)
    # a provisional edge between two members that will BOTH be merged into F
    eid = s.graph.add_transition(Transition(source=members[5], target=members[7],
                                            action=Action(selector=Selector("text", "x"), action_type="click")))
    s.provisional.append(eid)
    s.current_id = members[5]
    from wendle.capture.types import Snapshot
    from wendle.capture.hierarchy import parse_hierarchy
    s.current_snapshot = Snapshot(0.0, 0.0, "", parse_hierarchy(_twin("Churn 5")))
    s.record_gesture(_tap())  # new chrome -> coarsen
    # the provisional string now points at a LIVE edge -> reject_edge actually removes it
    prov = s.provisional[0]
    u, rest = prov.split("->"); v, k = rest.split("#")
    assert s.graph.g.has_edge(u, v, int(k))   # not a dead key
    n_before = s.graph.g.number_of_edges()
    s.reject_edge(prov)
    assert s.graph.g.number_of_edges() == n_before - 1  # the human's reject took effect (no zombie)


def test_human_merge_coarsen_does_not_orphan_a_staged_credential():
    # MEDIUM re-verification finding: mark_same's refined-twin coarsen repaired only current_id,
    # not the pending typing tags -> a credential staged on a merged member was orphaned & dropped.
    from wendle.fingerprint.signature import structure_id, refined_id, chrome_digest
    F = _coarse_F()
    struct = structure_id(NS, _twin("Network"), CFG_FOCUS)
    s = RecordSession(_driver((_twin("Network"), NS)), PROFILE, settle_kwargs=NOSLEEP)
    members = _seed_full_family(s, F, struct, 3)
    s._pending.append((members[2], NS, Action(selector=Selector("resource_id", "com.app:id/pw"),
                                              action_type="set_text", value={"param": "pw"}, sensitive=True)))
    s.mark_same(members[0], members[1])  # human merges two twins -> coarsen the family
    # the credential tagged members[2] was retagged to F, not orphaned on the dead member id
    assert all(sid == F for (sid, _n, _a) in s._pending)
    drained = s._take_pending(F)
    assert len(drained) == 1 and drained[0].selector.value == "com.app:id/pw"  # drainable on F
