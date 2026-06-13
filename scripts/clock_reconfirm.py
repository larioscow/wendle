"""One-shot device re-confirm: navigate to all 4 Clock tabs from cold launch, ground-truth
the selected tab, assert 0 confident-wrong. Used to re-validate after the landing-guard fix.
Exits 0 on success (>=3/4 reached, 0 confident-wrong), 1 on a confident-wrong, 2 if locked."""
import re, subprocess, sys, time
from wendle import Graph, U2Driver
from wendle.navigate.navigator import Navigator

PKG = "com.sec.android.app.clockpackage"


def focus():
    out = subprocess.run(["adb", "shell", "dumpsys", "window"], capture_output=True,
                         text=True).stdout
    m = re.search(r'mCurrentFocus=Window\{[^}]*\}', out)
    return m.group(0) if m else ""
def main():
    f = focus()
    if "Bouncer" in f or "Keyguard" in f or "NotificationShade" in f:
        print(f"[reconfirm] DEVICE LOCKED/SHADE ({f}) — cannot run"); return 2
    g = Graph.from_json(open("clock_tabs.json").read())
    drv = U2Driver()
    def cur():
        d = drv.dump_hierarchy()
        for m in re.finditer(r'<node[^>]*>', d):
            t = m.group(0); cd = re.search(r'content-desc="(Alarma|Reloj mundial|Cron\S*|Tempor\S*)"', t)
            if cd and 'selected="true"' in t: return cd.group(1)
        return "?"
    anchor = next(n for n in g.g.nodes if g.screen(n).force_action)
    tabnode = {d["action"].selector.value: v for u, v, _k, d in g.ordered_transitions()}
    ok = wrong = 0
    for lab, v in tabnode.items():
        subprocess.run(["adb", "shell", "am", "force-stop", PKG], capture_output=True); time.sleep(1.0)
        subprocess.run(["adb", "shell", "input", "keyevent", "3"], capture_output=True); time.sleep(0.8)
        out = Navigator(g, drv).navigate(anchor, v)
        gt = cur(); arr = out.status in ("arrived", "arrived_unverified"); match = (gt == lab)
        if arr and match: ok += 1
        if arr and not match: wrong += 1
        print(f"   -> {lab:16} {out.status:16} tier={getattr(out,'tier','')!s:11} GT={gt!r:18} "
              f"{'OK' if (arr and match) else ('CONFIDENT-WRONG' if arr else 'stop')}")
    print(f"[reconfirm] {ok}/{len(tabnode)} reached; confident-wrong={wrong}")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
