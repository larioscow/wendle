from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional, Tuple, Union


@dataclass(frozen=True)
class AbsAxis:
    """One absolute axis of an input device, with its reported value range."""

    min: int
    max: int


@dataclass
class InputDevice:
    """An entry from `getevent -lp`: a /dev/input node and its labeled ABS axes."""

    path: str
    name: str
    abs_axes: Dict[str, AbsAxis] = field(default_factory=dict)


@dataclass
class DeviceProfile:
    """Per-device calibration, persisted to JSON (structure only; no callables)."""

    touchscreen_node: str
    abs_x: Tuple[int, int]
    abs_y: Tuple[int, int]
    display: Tuple[int, int]
    timebase_validated: bool = False
    touch_protocol: str = "btn_touch"  # evdev MT protocol: type_b / btn_touch

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, blob: str) -> "DeviceProfile":
        d = json.loads(blob)
        return cls(
            touchscreen_node=d["touchscreen_node"],
            abs_x=tuple(d["abs_x"]),
            abs_y=tuple(d["abs_y"]),
            display=tuple(d["display"]),
            timebase_validated=d.get("timebase_validated", False),
            touch_protocol=d.get("touch_protocol", "btn_touch"),
        )


@dataclass(frozen=True)
class Selector:
    """A stable handle to a UI element. `kind` follows the §8 ladder.

    kind ∈ {content_desc, text, resource_id, xpath, coords}; for `coords`,
    `value` is an (x, y) pixel tuple, otherwise a string.
    """

    kind: str
    value: Union[str, Tuple[int, int]]


@dataclass
class Action:
    """A recorded UI action (§4). Secret literals are never stored — a
    `sensitive` action carries only a `{param: name}` handle in `value`.

    `action_type` ∈ {click, long_click, swipe, set_text, keyevent}. `scroll`
    is reserved for a future gesture path and is NOT produced by v1 gesture
    segmentation (§5); do not assume it appears.
    """

    selector: Selector
    action_type: str
    value: Optional[Dict[str, Any]] = None  # {"param": "<field>"} or {"text": ...}
    sensitive: bool = False
    replayability: str = "high"  # high / medium / coordinate_only
    end: Optional[Tuple[int, int]] = None  # swipe end point (pixels), if a swipe
    # navigate: a state-changing action that became a graph edge.
    # reveal/probe: an intra-screen action (scroll that revealed content, presence
    # check) that did NOT change state — stored on the screen, NOT offered as a
    # presence-probe selector (§ effectiveness filter). NOT routed as a TAP — but a
    # reveal-classified chrome-fork scroll IS carried on a `scroll`-class continuation
    # edge (Cap 1), which the navigator WALKS (reveal.walk_to_node) and replay SKIPS;
    # it is never executed as a navigate tap.
    intent: str = "navigate"  # navigate | reveal | probe
    # §7 (lazy-region design): the bound element's bounds from the settled bind frame, and
    # whether it lay inside a detected adapter region — the reveal rung's eligibility (§3.1)
    # and container-binding (§3.3b) inputs. Legacy actions: None/False -> reveal never fires.
    bounds: Optional[Tuple[int, int, int, int]] = None
    in_region: bool = False

    def __repr__(self) -> str:
        # Defense-in-depth: even though a sensitive action stores only a
        # {param: …} handle, never let a sensitive value/selector print verbatim.
        if self.sensitive:
            return (
                f"Action(action_type={self.action_type!r}, selector=<redacted>, "
                f"value=<redacted>, sensitive=True, replayability={self.replayability!r})"
            )
        return (
            f"Action(action_type={self.action_type!r}, selector={self.selector!r}, "
            f"value={self.value!r}, sensitive=False, replayability={self.replayability!r})"
        )

    __str__ = __repr__


@dataclass
class Transition:
    """A recorded edge between two screens (§4). Hooks are kept separately."""

    source: str
    target: str
    action: Action
    weight: float = 1.0
    action_class: str = "navigate"  # navigate | swipe | system_key (routing-cost class)
    # set_text steps that must run BEFORE this edge's action (e.g. fill username +
    # password, then this edge is the submit tap). They ride on the submit edge so the
    # Navigator — which routes over EDGES, not Screen.actions — actually replays them.
    pre_actions: List[Action] = field(default_factory=list)
    needs_confirmation: bool = False  # LOW-confidence binding (§5.1), provisional
    # Record-time wrong-merge tripwire (lazy-region design §2.8): a navigate-intent action whose
    # source and target resolved to the SAME id while the raw region content differed materially.
    # Navigation over a suspect edge reports arrived_unverified, never a confident arrival.
    suspect_self_loop: bool = False
    # GLOBAL NAV affordance (tab bar / bottom nav / drawer button): this edge's action is a
    # section-switch present on ~every screen of the app. Routing may walk it from ANY in-app
    # screen where the affordance resolves (verify-BY-AFFORDANCE), reaching content-drifting
    # sections (a live-clock tab) that no fingerprint can match. NEVER set on a suspect/
    # provisional edge. Confirmed by the affordance becoming the current item, never a fingerprint.
    global_affordance: bool = False
    # Spike-2 -> Spike-3 seam for APE-inspired mis-merge refinement:
    settled: bool = False  # the source/target screens were captured settled (§7 settle)
    landed_on_real_element: bool = False  # the tap hit a real element (over-merge signal, data only)


@dataclass(frozen=True)
class ForceAction:
    """A verified direct way to reach a screen (§6). Verified-at-record-time only:
    `verified_fp` is the EXACT fingerprint observed when this force was confirmed to
    land — the only trust boundary (am start's exit code lies)."""

    kind: str  # "keyevent" | "am_start"
    value: str  # "3" | "pkg/.MainActivity"
    verified_fp: Optional[str] = None
    # How the recorder reached this anchor — lets the launch ladder ORDER its rungs by provenance
    # instead of blindly probing a known-doomed component:
    #   "self_routing"   the anchor was DEFERRED past a non-interactive splash (the recorded
    #                     activity is a deep, typically NON-EXPORTED surface) — `am start -n` of it
    #                     is refused, so the ladder SKIPS RecordedComponent and lets the package
    #                     default route the splash in (BanCoppel).
    #   "launcher_entry" reached directly from the launcher onto an interactive screen — the
    #                     recorded component may be launchable; try it first.
    #   "unknown"        hand-built / legacy anchor — default ladder order.
    provenance: str = "unknown"

    @property
    def verified(self) -> bool:
        return self.verified_fp is not None


@dataclass
class Screen:
    """A node in the navigation graph (§4). `id` is the fingerprint, or
    "V"+sha1(namespace) for a never-settle (volatile) screen."""

    id: str
    namespace: str
    structure_id: Optional[str] = None  # coarse text-free tier (§ DroidBot structure_str)
    # Collision-driven twin refinement (task #17b). Both None on an unrefined/legacy screen ->
    # identity behaves exactly as today. When a structure-twin family is refined apart, each
    # member carries the chrome digest that distinguishes it and the coarse fingerprint they
    # share (so a refined id = refined_id(coarse_id, chrome_digest) is recomputable at navigate
    # time). A non-None coarse_id is the marker that this id is a REFINED twin.
    chrome_digest: Optional[str] = None
    coarse_id: Optional[str] = None
    # Whether the screen was RECORDED adapter-list-dominant (its leaves are mostly a RecyclerView,
    # so a refined twin's chrome digest reduces to the toolbar title). An IDENTITY-CLASS property
    # fixed at record time — the navigator's HW2 gate keys on THIS, not the live observed row count
    # (which an empty/sparse list would flip, bypassing the guard — adversarial finding).
    adapter_dominant: bool = False
    # L3 (lazy-region design §2.5): True iff at least one text/desc VALUE outside every detected
    # adapter region survived into this screen's fingerprint hash, computed at record time from
    # the settled dump. On-sight confidence keys on THIS recorded fact — never on the profile
    # alone (a repetition-dominant include_text screen's id is reproducible by an unrecorded
    # sibling). None = legacy/unknown -> treated as NOT value-bearing (corroboration required).
    value_bearing: Optional[bool] = None
    screen_type: str = "app"  # homescreen|app|settings|systemui|lockscreen
    package: Optional[str] = None
    activity: Optional[str] = None
    profile_name: str = "view"  # view|compose|launcher|volatile (STORED at resolve time)
    fingerprint_confidence: str = "high"  # high|low
    volatile: bool = False
    actions: List[Action] = field(default_factory=list)  # navigate-intent (routed) actions
    # Intra-screen actions that did NOT change state (effectiveness filter): scrolls
    # that revealed content (reveal) and presence-check taps (probe). Recorded as data
    # for higher layers; NEVER routed and NEVER used as presence probes.
    intra_actions: List[Action] = field(default_factory=list)
    force_action: Optional[ForceAction] = None
    hierarchy: Optional[str] = None  # OFF by default (raw hierarchy = PII)
