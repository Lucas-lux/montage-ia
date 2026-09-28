"""Pages HTML des scènes d'un montage court (écran partagé, papier, habillage).

Chaque scène qui a quelque chose à montrer devient une page HTML de la taille
de sa zone graphique, animée par des Web Animations (et `window.seek(t)` pour
les compteurs), que `visuals.render_animation` capture image par image. Rien
n'est tiré au hasard, rien ne dépend de l'heure : deux rendus de la même scène
donnent les mêmes images (cache par empreinte, voir scenes.py).

Repères : les éléments (`items`) se placent en pixels de la ZONE de la scène
(x, y = bord haut-gauche ; « center » centre horizontalement). Les temps
(`at`, `strike_at`…) arrivent déjà relatifs au début de la scène, en secondes.

Deux looks :
  * « clean » (défaut) : fond clair de la marque, cartes blanches, un seul
    accent, grotesque grasse (Inter) — l'écran partagé des créateurs ;
  * « paper » : papier chaud, grain et quadrillage, cartes de travers avec
    une feuille kraft derrière, titres Archivo, accent en Fraunces italique,
    annotations à la main (Caveat) — l'explication « magazine ».
"""
from __future__ import annotations

import html
import json
import re

# Courbes : « back » dépasse la cible puis y revient (entrée qui claque).
def _back_out(s: float, n: int = 24) -> str:
    pts = []
    for i in range(n + 1):
        u = i / n - 1.0
        pts.append(1 + (s + 1) * u ** 3 + s * u ** 2)
    return "linear(" + ", ".join(f"{p:.4f}" for p in pts) + ")"


EASE = {
    "linear": "linear",
    "out": "cubic-bezier(0.215, 0.61, 0.355, 1)",        # sortie cubique
    "out2": "cubic-bezier(0.25, 0.46, 0.45, 0.94)",
    "out4": "cubic-bezier(0.165, 0.84, 0.44, 1)",
    "in": "cubic-bezier(0.55, 0.055, 0.675, 0.19)",
    "inout": "cubic-bezier(0.645, 0.045, 0.355, 1)",
    "sine": "cubic-bezier(0.37, 0, 0.63, 1)",
    "back": _back_out(1.5),
    "back2": _back_out(2.2),
    "back3": _back_out(2.6),
    "steps": "steps(6, end)",
}

FONT = {  # polices livrées (engine/data/fonts), sous leur nom affiché
    "clean": {"text": "Inter Bold", "heavy": "Inter ExtraBold", "display": "Inter ExtraBold",
              "serif": "Fraunces Black Italic", "hand": "Caveat Bold"},
    "paper": {"text": "Archivo Bold", "heavy": "Archivo ExtraBold", "display": "Archivo Black",
              "serif": "Fraunces Black Italic", "hand": "Caveat Bold"},
}
DEFAULT_BRAND = {
    "clean": {"bg": "#F3F1EE", "ink": "#0F0D0D", "accent": "#D40F30", "muted": "#8F8B85",
              "soft": "#FDEAE6", "soft2": "#F5C2C9", "card": "#FFFFFF"},
    "paper": {"bg": "#EFE9DC", "ink": "#17130E", "accent": "#F26A1B", "muted": "#8A8174",
              "soft": "#E6DCC4", "soft2": "#D9CBAE", "card": "#F7F2E7", "alarm": "#E23B2E"},
}

_RICH = re.compile(r"(</?em>|<br\s*/?>|</?b>)", re.I)


def rich(s) -> str:
    """Texte échappé où seuls <em>, <b> et <br> passent."""
    parts = _RICH.split(str(s or ""))
    return "".join(p.lower().replace(" ", "") if _RICH.fullmatch(p) else html.escape(p) for p in parts)


def esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


def _px(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _f(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") or "0"


class Page:
    """Une page de scène en construction : éléments, animations, compteurs."""

    def __init__(self, w: int, h: int, look: str, brand: dict, locale: str = "fr-FR") -> None:
        self.w, self.h = int(w), int(h)
        self.look = look if look in FONT else "clean"
        self.brand = {**DEFAULT_BRAND[self.look], **{k: v for k, v in (brand or {}).items() if v}}
        self.font = {**FONT[self.look], **{k: brand[k] for k in ("text", "heavy", "display", "serif", "hand")
                                            if (brand or {}).get(k)}}
        self.locale = locale or "fr-FR"
        self.opaque = False                 # zone pleine (fond de la marque) : contraste mesurable
        self.body: list[str] = []
        self.anims: list[dict] = []
        self.counters: list[dict] = []
        self.n = 0
        self.events: list[dict] = []        # instants où quelque chose se passe (validation du rythme)

    # -------------------------------------------------------------- outils
    def nid(self) -> str:
        self.n += 1
        return f"e{self.n}"

    def anim(self, sel: str, kf: list[dict], at: float, dur: float, ease: str = "out", fill: str = "both") -> None:
        self.anims.append({"s": sel, "k": kf, "t": round(max(0.0, at), 3), "d": round(max(0.01, dur), 3),
                           "e": EASE.get(ease, ease), "f": fill})

    def event(self, t: float, kind: str, label: str = "") -> None:
        self.events.append({"t": round(max(0.0, t), 3), "type": kind, "label": label})

    def enter(self, sel: str, at: float, frm: str = "up", dur: float = 0.5, ease: str = "back") -> None:
        """Entrée d'un élément : il arrive d'un côté, ou grossit, ou tombe."""
        start = {"left": {"translate": "-520px 0", "rotate": "-4deg"}, "right": {"translate": "520px 0", "rotate": "4deg"},
                 "up": {"translate": "0 90px"}, "down": {"translate": "0 -90px"}, "far": {"scale": "0.6"},
                 "pop": {"scale": "0.3", "rotate": "-18deg"}, "fade": {},
                 "stamp": {"scale": "1.8", "rotate": "-14deg"}}.get(frm, {"translate": "0 90px"})
        end = {k: ("0 0" if k == "translate" else "1" if k == "scale" else "0deg") for k in start}
        self.anim(sel, [{"opacity": 0, **start}, {"opacity": 1, **end}], at, dur, ease)

    def counter(self, sel: str, value: float, at: float, dur: float, suffix: str = "", prefix: str = "",
                decimals: int = 0) -> None:
        self.counters.append({"s": sel, "v": value, "t": round(at, 3), "d": round(max(0.05, dur), 3),
                              "sfx": suffix, "pfx": prefix, "dec": int(decimals)})

    def num(self, value: float, suffix: str = "", prefix: str = "", decimals: int = 0) -> str:
        """Nombre formaté comme le fera le compteur (espace fine → espace)."""
        s = f"{value:,.{decimals}f}"
        if self.locale.startswith(("fr", "de", "es", "it", "pt", "ru", "pl", "sv", "nb", "da", "fi")):
            s = s.replace(",", " ").replace(".", ",") if self.locale.startswith("fr") else s.replace(",", ".")
        return f"{prefix}{s}{suffix}"

    # -------------------------------------------------------------- rendu
    def css(self) -> str:
        b, f = self.brand, self.font
        grain = ("url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='220' height='220'%3E"
                 "%3Cfilter id='g'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' "
                 "seed='11' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E"
                 "%3Crect width='100%25' height='100%25' filter='url(%23g)'/%3E%3C/svg%3E\")")
        return f"""
:root {{ --bg:{b['bg']}; --ink:{b['ink']}; --accent:{b['accent']}; --accent-ink:{b.get('accent_ink', b['accent'])};
  --muted:{b['muted']}; --soft:{b['soft']}; --soft2:{b['soft2']}; --card:{b['card']}; --alarm:{b.get('alarm', b['accent'])};
  --f-text:"{f['text']}"; --f-heavy:"{f['heavy']}"; --f-display:"{f['display']}"; --f-serif:"{f['serif']}";
  --f-hand:"{f['hand']}"; --grain:{grain}; }}
* {{ font-weight: 400; }}
body {{ color: var(--ink); font-family: var(--f-text), Arial, sans-serif; }}
.bg {{ position:absolute; inset:0; background:var(--bg); overflow:hidden; }}
.bg.grad {{ background: linear-gradient(135deg, var(--bg) 0%, var(--bg) 55%, var(--soft) 80%, var(--soft2) 100%); }}
.bg .shine {{ position:absolute; inset:0; background: radial-gradient(120% 80% at 50% 0%, rgba(255,255,255,.5), transparent 62%); }}
.bg.paper::before {{ content:""; position:absolute; inset:0; background-image:var(--grain); opacity:.3; mix-blend-mode:multiply; }}
.bg.paper::after {{ content:""; position:absolute; inset:0; background-size:64px 64px;
  background-image: linear-gradient(rgba(23,19,14,.06) 1px, transparent 1px), linear-gradient(90deg, rgba(23,19,14,.06) 1px, transparent 1px); }}
.bg img.fill {{ position:absolute; inset:-4%; width:108%; height:108%; object-fit:cover; }}
.bg .mesh {{ position:absolute; inset:-6%;
  background: radial-gradient(ellipse 75% 60% at 15% 20%, color-mix(in srgb, var(--accent) 28%, transparent), transparent 62%),
    radial-gradient(ellipse 65% 50% at 85% 12%, color-mix(in srgb, var(--accent) 15%, white), transparent 64%),
    radial-gradient(ellipse 90% 70% at 75% 88%, color-mix(in srgb, var(--accent) 20%, transparent), transparent 66%),
    radial-gradient(ellipse 60% 50% at 25% 90%, rgba(255,255,255,.75), transparent 62%),
    linear-gradient(160deg, #FFFCFA 0%, color-mix(in srgb, var(--accent) 7%, #FAF4F0) 55%, color-mix(in srgb, var(--accent) 14%, #F6EAE3) 100%); }}
.it {{ position:absolute; }}
.card {{ background:#fff; border-radius:26px; overflow:hidden;
  box-shadow: 0 2px 0 rgba(15,13,13,.06), 0 24px 60px rgba(15,13,13,.16); }}
.paperlook .card {{ background:var(--card); border-radius:3px; rotate:-1.4deg;
  box-shadow: 0 2px 0 rgba(23,19,14,.08), 8px 12px 24px rgba(23,19,14,.18), 14px 18px 0 -6px var(--soft); }}
.card .bar {{ height:14px; background:var(--accent); }}
.paperlook .card .bar {{ display:none; }}
.card .ttl {{ padding:26px 30px 0; font-family:var(--f-heavy); font-size:40px; line-height:1.05; letter-spacing:-.02em; }}
.card .ttl em, .statement .big em, .headline em, .mega em, .kw em {{ font-style:normal; color:var(--accent-ink); }}
.paperlook .headline em, .paperlook .mega em {{ font-family:var(--f-serif); }}
.card .num {{ padding:16px 30px 0; font-family:var(--f-display); font-size:96px; line-height:1; letter-spacing:-.04em;
  color:var(--accent); font-variant-numeric:tabular-nums; }}
.card .lab {{ padding:10px 30px 28px; font-family:var(--f-text); font-size:25px; letter-spacing:.14em; color:var(--muted); }}
.card .logos {{ display:grid; gap:18px 14px; padding:22px 26px 28px; align-items:center; justify-items:center; }}
.card .logos img {{ width:100%; object-fit:contain; }}
.badge {{ width:120px; height:120px; border-radius:50%; display:grid; place-items:center; background:var(--accent);
  color:#fff; font-family:var(--f-display); font-size:44px; font-style:italic;
  box-shadow: 0 16px 40px color-mix(in srgb, var(--accent) 40%, transparent); }}
.stamp {{ padding:16px 32px; border:8px solid var(--accent); border-radius:18px; color:var(--accent); rotate:-6deg;
  font-family:var(--f-display); font-size:62px; letter-spacing:-.02em; background:rgba(255,255,255,.72); white-space:nowrap; }}
.line {{ font-family:var(--f-text); font-size:34px; letter-spacing:.04em; color:var(--muted); white-space:nowrap; }}
.big {{ text-align:center; font-family:var(--f-display); letter-spacing:-.04em; line-height:1; color:var(--accent);
  font-variant-numeric:tabular-nums; }}
.big .v {{ display:inline-block; position:relative; padding:0 30px; }}
.big .lab {{ font-family:var(--f-text); font-size:30px; letter-spacing:.14em; color:var(--muted); margin-top:18px; }}
.ring {{ position:absolute; inset:-16px -30px; border:10px solid var(--alarm); border-radius:50%; rotate:-4deg; }}
.strike {{ position:absolute; height:12px; background:var(--alarm); border-radius:6px; transform-origin:0 50%; }}
.check {{ display:flex; align-items:center; gap:22px; background:#fff; border-radius:22px; padding:22px 30px;
  box-shadow:0 18px 44px rgba(15,13,13,.14); font-family:var(--f-text); font-size:44px; letter-spacing:-.01em; }}
.paperlook .check {{ background:var(--card); border-radius:4px; rotate:-1deg; }}
.check .n {{ color:var(--accent); font-family:var(--f-display); font-size:34px; }}
.check .ck {{ margin-left:auto; flex:none; width:56px; height:56px; border-radius:50%; background:var(--accent); color:#fff;
  display:grid; place-items:center; font-family:var(--f-display); font-size:34px; }}
.check .ul {{ position:absolute; left:30px; right:30px; bottom:14px; height:8px; background:var(--accent);
  border-radius:4px; transform-origin:0 50%; }}
.shot {{ overflow:hidden; border-radius:18px; background:#fff; box-shadow:0 24px 60px rgba(15,13,13,.18); }}
.shot .in {{ position:relative; }}
.shot img {{ display:block; width:100%; height:auto; }}
.shot .hl {{ position:absolute; border:6px solid var(--accent); border-radius:8px;
  background:color-mix(in srgb, var(--accent) 16%, transparent); transform-origin:0 50%; }}
.browser {{ overflow:hidden; border-radius:10px; background:#fff; box-shadow:0 2px 0 rgba(23,19,14,.08), 8px 12px 24px rgba(23,19,14,.18); rotate:-1.4deg; }}
.browser .top {{ height:46px; display:flex; align-items:center; gap:8px; padding:0 16px; background:#EAE4D6;
  font-family:var(--f-text); font-size:18px; color:var(--muted); }}
.browser .top i {{ width:12px; height:12px; border-radius:50%; background:rgba(23,19,14,.25); display:inline-block; }}
.browser .win {{ position:relative; overflow:hidden; }}
.browser .win img {{ display:block; width:100%; height:auto; }}
.photo {{ overflow:hidden; border-radius:28px; background:#fff; box-shadow:0 30px 80px rgba(15,13,13,.22); }}
.photo img {{ display:block; width:100%; height:100%; object-fit:cover; }}
.logo img {{ display:block; width:100%; height:auto; }}
.chev {{ display:block; text-align:center; font-family:var(--f-display); font-size:150px; line-height:.55; color:var(--accent); }}
.glass {{ border-radius:30px; padding:30px 34px 32px; background:rgba(255,255,255,.62); border:1.5px solid rgba(255,255,255,.88);
  box-shadow:0 30px 70px rgba(23,19,14,.16); backdrop-filter:blur(22px); }}
.glass.dark {{ background:rgba(24,26,32,.74); border-color:rgba(255,255,255,.16); color:#fff; }}
.glass .ico {{ width:82px; height:82px; border-radius:22px; display:grid; place-items:center; font-family:var(--f-display);
  font-size:40px; background:var(--accent); color:#fff; }}
.glass .ttl {{ margin-top:22px; font-family:var(--f-heavy); font-size:52px; line-height:1.02; letter-spacing:-.025em; }}
.glass .nb {{ margin-top:16px; font-family:var(--f-display); font-size:96px; line-height:1; letter-spacing:-.04em; color:var(--accent); }}
.glass .sub {{ margin-top:12px; font-size:20px; letter-spacing:.16em; text-transform:uppercase; color:var(--muted); }}
.glass .bars {{ margin-top:24px; height:150px; display:flex; align-items:flex-end; gap:14px; }}
.glass .bars i {{ flex:1; background:var(--accent); border-radius:8px 8px 3px 3px; transform-origin:50% 100%; }}
.glass .bars i:nth-child(odd) {{ opacity:.55; }}
.glass .pic {{ margin-top:22px; border-radius:16px; overflow:hidden; }}
.glass .pic img {{ display:block; width:100%; }}
.statement .kick {{ font-family:var(--f-heavy); font-size:26px; letter-spacing:.18em; text-transform:uppercase; color:var(--muted); }}
.statement .big {{ margin-top:18px; text-align:left; color:var(--ink); font-size:104px; line-height:1.02; letter-spacing:-.038em; }}
.statement .s2 {{ margin-top:18px; font-size:42px; line-height:1.2; color:#55504A; }}
.pill {{ display:flex; align-items:center; gap:20px; padding:20px 24px 20px 34px; border-radius:24px; font-size:36px;
  background:rgba(255,255,255,.7); border:1.5px solid rgba(255,255,255,.9); box-shadow:0 24px 60px rgba(23,19,14,.16); }}
.pill .arw {{ width:56px; height:56px; border-radius:17px; display:grid; place-items:center; background:var(--accent);
  color:#fff; font-family:var(--f-display); font-size:28px; }}
.tag {{ display:flex; align-items:center; gap:12px; padding:14px 26px; border-radius:999px; font-size:30px; white-space:nowrap;
  background:rgba(255,255,255,.75); border:1.5px solid rgba(255,255,255,.9); box-shadow:0 16px 40px rgba(23,19,14,.14); }}
.tag .dot {{ width:14px; height:14px; border-radius:50%; background:var(--accent); }}
.tag img {{ width:40px; height:40px; object-fit:contain; }}
.lcard {{ display:flex; align-items:center; gap:26px; padding:26px 36px 26px 26px; border-radius:28px; background:rgba(255,255,255,.78);
  border:1.5px solid rgba(255,255,255,.92); box-shadow:0 26px 64px rgba(23,19,14,.18); }}
.lcard .mk {{ flex:none; width:96px; height:96px; border-radius:26px; background:#fff; display:grid; place-items:center;
  box-shadow:0 10px 24px rgba(23,19,14,.12); }}
.lcard .mk img {{ width:64px; height:64px; object-fit:contain; }}
.lcard .nm {{ font-family:var(--f-heavy); font-size:46px; letter-spacing:-.025em; line-height:1; }}
.lcard .rw {{ display:flex; gap:10px; margin-top:12px; }}
.lcard .ch {{ font-size:27px; color:#55504A; background:rgba(23,19,14,.07); border-radius:999px; padding:8px 18px; white-space:nowrap; }}
.lcard .bd {{ font-family:var(--f-heavy); font-size:19px; letter-spacing:.12em; color:#fff; background:var(--accent);
  border-radius:999px; padding:8px 14px; white-space:nowrap; }}
.cascade {{ perspective:2200px; }}
.cascade .cd {{ position:absolute; top:0; overflow:hidden; border:3px solid rgba(255,255,255,.6);
  box-shadow:0 34px 90px rgba(23,19,14,.34); background:#000; }}
.cascade .cd img {{ display:block; width:100%; height:100%; object-fit:cover; }}
.kicker {{ display:flex; justify-content:space-between; font-family:var(--f-text); font-size:26px; letter-spacing:.16em;
  text-transform:uppercase; color:var(--muted); }}
.headline {{ font-family:var(--f-display); font-size:96px; line-height:.94; letter-spacing:-.03em; text-transform:uppercase; }}
.sub {{ font-family:var(--f-serif); font-size:54px; line-height:1.05; color:var(--accent-ink); }}
.note {{ font-family:var(--f-hand); font-size:56px; color:var(--alarm); rotate:-6deg; white-space:nowrap; }}
.rows {{ padding:30px 38px; }}
.rows .t {{ font-family:var(--f-text); font-size:24px; letter-spacing:.14em; text-transform:uppercase; color:var(--muted); margin-bottom:20px; }}
.rows .row {{ display:flex; align-items:center; justify-content:space-between; border-top:2px dashed rgba(23,19,14,.25);
  padding:22px 0; font-family:var(--f-display); font-size:44px; text-transform:uppercase; }}
.rows .row:last-child {{ border-bottom:2px dashed rgba(23,19,14,.25); }}
.rows .row .n {{ color:var(--accent-ink); margin-right:18px; }}
.rows .row .meta {{ font-family:var(--f-text); font-size:34px; color:var(--muted); text-transform:none; }}
.rows .ok {{ display:inline-grid; place-items:center; width:52px; height:52px; border-radius:50%; margin-left:16px;
  background:var(--accent); color:#fff; font-size:32px; border:3px solid var(--ink); }}
.rows .todo {{ display:inline-block; width:52px; height:52px; border-radius:50%; margin-left:16px; border:3px dashed rgba(23,19,14,.3); }}
.mega {{ font-family:var(--f-display); line-height:.86; letter-spacing:-.03em; text-transform:uppercase; }}
.mega .l {{ display:block; }}
.kw {{ padding:34px 40px 40px; }}
.kw .word {{ margin-top:26px; font-family:var(--f-display); font-size:118px; line-height:1; text-transform:uppercase; }}
.kw .word em {{ display:inline-block; color:var(--accent); }}
.ov {{ text-align:center; line-height:1.16; color:#fff; font-family:var(--f-heavy); letter-spacing:-.03em;
  text-shadow: 0 4px 10px rgba(0,0,0,.55), 0 10px 30px rgba(0,0,0,.4); }}
.ov .w {{ display:inline-block; position:relative; }}
.ov .lab {{ display:block; font-family:var(--f-text); font-size:34px; letter-spacing:.14em; margin-top:.22em; color:#D9D5CF; }}
.ov .strike {{ left:-20px; right:-20px; top:50%; background:var(--accent); rotate:-6deg; }}
.ov .ring {{ inset:-12px -34px; border-color:var(--accent); }}
.ov .ul {{ position:absolute; left:0; right:0; bottom:-6px; height:10px; background:var(--accent); transform-origin:0 50%; }}
.ov .ck {{ color:var(--accent); margin-right:18px; }}
.fshadow {{ position:absolute; border-radius:30px; box-shadow:0 34px 90px rgba(23,19,14,.34); }}
"""

    def html(self, markup_bg: str = "") -> str:
        anims = json.dumps(self.anims, ensure_ascii=False)
        counters = json.dumps(self.counters, ensure_ascii=False)
        cls = "paperlook" if self.look == "paper" else "cleanlook"
        # window.seek : compteurs ; window.activeAt : quelque chose bouge-t-il à t ? (le
        # moteur de capture réutilise l'image précédente quand rien ne bouge)
        script = f"""<script>
(function () {{
  const A = {anims}, C = {counters};
  for (const a of A) {{
    const el = document.querySelector(a.s);
    if (el) el.animate(a.k, {{ delay: a.t * 1000, duration: a.d * 1000, easing: a.e, fill: a.f,
                               iterations: a.i || 1, direction: a.dir || "normal" }});
  }}
  const OPAQUE = {json.dumps(self.opaque)}, BASE = {json.dumps(self.brand["bg"])};
  const loc = {json.dumps(self.locale)};
  const ease = (u) => 1 - Math.pow(1 - u, 2);
  const fmt = (v, c) => c.pfx + v.toLocaleString(loc, {{ minimumFractionDigits: c.dec, maximumFractionDigits: c.dec }})
    .replace(/\\u202f|\\u00a0/g, " ") + c.sfx;
  window.seek = (t) => {{
    for (const c of C) {{
      const el = document.querySelector(c.s);
      if (!el) continue;
      const u = Math.min(1, Math.max(0, (t - c.t) / c.d));
      const v = c.v * ease(u);
      el.textContent = fmt(c.dec ? v : Math.round(v), c);
    }}
  }};
  window.activeAt = (t) => C.some((c) => t >= c.t - 0.05 && t <= c.t + c.d + 0.05);
  window.__check = () => {{
    const out = [], W = innerWidth, H = innerHeight;
    const vis = (e) => (e.checkVisibility ? e.checkVisibility({{ opacityProperty: true, visibilityProperty: true }}) : true);
    const texts = [...document.querySelectorAll("[data-t]")].filter(vis);
    const box = (e) => e.getBoundingClientRect();
    for (const e of texts) {{
      const r = box(e);
      if (r.width < 1) continue;
      if (r.left < -2 || r.top < -2 || r.right > W + 2 || r.bottom > H + 2)
        out.push({{ type: "outside", item: e.dataset.t, text: e.textContent.trim().slice(0, 40) }});
      const bgc = (el) => {{ for (let x = el; x && x !== document.body; x = x.parentElement) {{ const c = getComputedStyle(x).backgroundColor;
        if (c && !/rgba\\(.*, 0\\)$/.test(c) && c !== "transparent") return c; }} return null; }};
      // fond derrière le texte : sa carte, sinon la zone si elle est pleine (au-dessus d'une vidéo : non mesuré)
      const bg = bgc(e) || (OPAQUE ? BASE : null);
      if (bg) out.push({{ type: "contrast", item: e.dataset.t, fg: getComputedStyle(e).color, bg,
                          text: e.textContent.trim().slice(0, 40) }});
    }}
    for (let i = 0; i < texts.length; i++) for (let j = i + 1; j < texts.length; j++) {{
      const a = texts[i], b = texts[j];
      if (a.dataset.t === b.dataset.t || a.dataset.float || b.dataset.float) continue;
      const p = box(a), q = box(b);
      const ix = Math.min(p.right, q.right) - Math.max(p.left, q.left), iy = Math.min(p.bottom, q.bottom) - Math.max(p.top, q.top);
      if (ix > 6 && iy > 6) out.push({{ type: "overlap", item: a.dataset.t, other: b.dataset.t,
                                        text: a.textContent.trim().slice(0, 30) + " / " + b.textContent.trim().slice(0, 30) }});
    }}
    out.push({{ type: "rects", rects: texts.map((e) => {{ const r = box(e); return [e.dataset.t, r.left, r.top, r.width, r.height]; }}) }});
    return out;
  }};
  window.seek(0);
}})();
</script>"""
        return (f'<!doctype html><html><head><meta charset="utf-8"><style>{self.css()}</style></head>'
                f'<body class="{cls}" style="width:{self.w}px;height:{self.h}px;position:relative;overflow:hidden">'
                f'{markup_bg}{"".join(self.body)}{script}</body></html>')


# ------------------------------------------------------------------ fonds

def rounded_path(W: float, H: float, x: float, y: float, w: float, h: float, r: float) -> str:
    """Chemin SVG : le cadre entier moins un rectangle arrondi (règle evenodd)."""
    r = max(0.0, min(r, w / 2, h / 2))
    return (f"M0,0 H{_f(W)} V{_f(H)} H0 Z M{_f(x + r)},{_f(y)} H{_f(x + w - r)} "
            f"A{_f(r)},{_f(r)} 0 0 1 {_f(x + w)},{_f(y + r)} V{_f(y + h - r)} "
            f"A{_f(r)},{_f(r)} 0 0 1 {_f(x + w - r)},{_f(y + h)} H{_f(x + r)} "
            f"A{_f(r)},{_f(r)} 0 0 1 {_f(x)},{_f(y + h - r)} V{_f(y + r)} "
            f"A{_f(r)},{_f(r)} 0 0 1 {_f(x + r)},{_f(y)} Z")


def background(p: Page, kind: str, dur: float, holes: list[dict] | None = None, image: str = "",
               drift: bool = True) -> str:
    """Fond de la zone : uni / dégradé / papier / maillage / image. `holes` :
    fenêtres transparentes (le visage passe dessous), chacune sur sa plage de
    temps [{x, y, w, h, r, t0, t1}] ; hors de ces plages, le fond est plein."""
    cls = {"paper": "bg paper", "gradient": "bg grad", "mesh": "bg", "image": "bg"}.get(kind, "bg")
    inner = ""
    if kind == "mesh":
        inner = '<div class="mesh" data-drift></div>'
    elif kind == "image" and image:
        inner = f'<img class="fill" data-drift src="{esc(image)}" alt="">'
    elif kind != "paper":
        inner = '<div class="shine"></div>'
    layers = []
    windows = holes or [None]
    for k, hole in enumerate(windows):
        bid = f"bg{k}"
        style = ""
        if hole:
            style = f' style="clip-path:path(evenodd, &quot;{rounded_path(p.w, p.h, hole["x"], hole["y"], hole["w"], hole["h"], hole.get("r", 0))}&quot;)"'
        layers.append(f'<div class="{cls}" id="{bid}" data-opaque{style}>{inner}</div>')
        if hole and len(windows) > 1:
            # un seul fond visible à la fois : bascule sèche à chaque déplacement du visage
            t0, t1 = hole.get("t0", 0.0), hole.get("t1", dur)
            p.anim(f"#{bid}", [{"opacity": 0}, {"opacity": 1}], t0, 0.01, "linear")
            if t1 < dur - 0.01:
                p.anim(f"#{bid}", [{"opacity": 1}, {"opacity": 0}], t1, 0.01, "linear", fill="forwards")
        if hole and hole.get("w") and hole.get("h") and hole.get("shadow", True) and hole.get("w") < p.w - 1:
            sid = f"fs{k}"
            layers.append(f'<div class="fshadow" id="{sid}" style="left:{_f(hole["x"])}px;top:{_f(hole["y"])}px;'
                          f'width:{_f(hole["w"])}px;height:{_f(hole["h"])}px;border-radius:{_f(hole.get("r", 0))}px"></div>')
            if len(windows) > 1:
                p.anim(f"#{sid}", [{"opacity": 0}, {"opacity": 1}], hole.get("t0", 0.0), 0.01, "linear")
                if hole.get("t1", dur) < dur - 0.01:
                    p.anim(f"#{sid}", [{"opacity": 1}, {"opacity": 0}], hole["t1"], 0.01, "linear", fill="forwards")
    if drift and kind in ("mesh", "image"):
        for k in range(len(windows)):
            p.anim(f"#bg{k} [data-drift]", [{"scale": "1"}, {"scale": "1.05"}], 0, max(0.1, dur), "linear")
    return "".join(layers)


# ---------------------------------------------------------------- éléments

def _xy(p: Page, it: dict, w: float | None = None) -> tuple[float | None, float]:
    """(x, y) du bord haut-gauche ; x None = toute la largeur (texte centré)."""
    y = _px(it.get("y"), 120)
    x = it.get("x", 60)
    if x is None:
        return None, y
    if x == "center":
        return ((p.w - w) / 2 if w else None), y
    return _px(x, 60), y


def _pos(p: Page, it: dict, w: float | None = None, extra: str = "") -> str:
    x, y = _xy(p, it, w)
    style = f"top:{_f(y)}px;"
    style += "left:0;right:0;" if x is None else f"left:{_f(x)}px;"
    if w:
        style += f"width:{_f(w)}px;"
    if it.get("h"):
        style += f"height:{_f(_px(it['h']))}px;"
    return style + extra


def _decor(p: Page, iid: str, it: dict, t) -> None:
    """Gestes communs après l'entrée : barrer, entourer, souligner, estomper, sortir."""
    for key, sel, kf, ease in (
        ("strike_at", f"#{iid}-strike", [{"scale": "0 1"}, {"scale": "1 1"}], "out"),
        ("circle_at", f"#{iid}-ring", [{"opacity": 0, "scale": "0.3"}, {"opacity": 1, "scale": "1"}], "back2"),
        ("underline_at", f"#{iid}-ul", [{"scale": "0 1"}, {"scale": "1 1"}], "out"),
    ):
        if it.get(key) is not None:
            at = t(it[key])
            p.anim(sel, kf, at, 0.3, ease)
            p.event(at, key[:-3], it.get("text") or it.get("title") or "")
    if it.get("dim_at") is not None:
        at = t(it["dim_at"])
        p.anim(f"#{iid}", [{"opacity": 1, "scale": "1"},
                           {"opacity": float(it.get("dim_to", 0.55)), "scale": str(it.get("dim_scale", 0.72))}],
               at, 0.4, "inout", fill="forwards")
        p.event(at, "dim")
    if it.get("shrink_at") is not None:
        at = t(it["shrink_at"])
        p.anim(f"#{iid}", [{"scale": "1", "translate": "0 0"},
                           {"scale": str(it.get("shrink_scale", 0.62)),
                            "translate": f"{_px(it.get('shrink_x'))}px {_px(it.get('shrink_y'))}px"}],
               at, 0.5, "inout", fill="forwards")
        p.event(at, "shrink")
    if it.get("out_at") is not None:
        p.anim(f"#{iid}", [{"opacity": 1}, {"opacity": 0}], t(it["out_at"]), 0.25, "linear", fill="forwards")


def add_item(p: Page, it: dict, j: int, t) -> None:
    """Un élément de scène (voir le catalogue dans scenes.ITEM_HELP)."""
    typ = str(it.get("type") or "line")
    iid = f"i{j}"
    at = t(it.get("at", 0))
    label = it.get("text") or it.get("title") or it.get("value") or typ
    p.event(at, typ, str(label)[:40])
    B = p.body
    tt = f'data-t="{iid}"'
    if typ == "card":
        w = _px(it.get("w"), 460)
        logos = "".join(f'<img src="{esc(s)}" alt="" style="height:{_f(_px(it.get("logo_h"), 96))}px">'
                        for s in it.get("logos") or [])
        num = ""
        if it.get("value") is not None:
            v = _px(it["value"])
            shown = p.num(0 if it.get("count") else v, it.get("suffix", ""), it.get("prefix", ""),
                          int(it.get("decimals") or 0))
            num = f'<div class="num" id="{iid}-n" {tt}>{esc(shown)}</div>'
            if it.get("count"):
                p.counter(f"#{iid}-n", v, t(it.get("count_at", it.get("at", 0))), _px(it["count"], 0.9),
                          it.get("suffix", ""), it.get("prefix", ""), int(it.get("decimals") or 0))
        elif it.get("number"):
            num = f'<div class="num" {tt}>{esc(it["number"])}</div>'
        cols = int(it.get("cols") or 3)
        B.append(f'<div class="it card" id="{iid}" style="{_pos(p, it, w)}"><div class="bar"></div>'
                 + (f'<div class="ttl" {tt}>{rich(it["title"])}</div>' if it.get("title") else "") + num
                 + (f'<div class="lab" {tt}>{esc(it["label"])}</div>' if it.get("label") else "")
                 + (f'<div class="logos" style="grid-template-columns:repeat({cols},minmax(0,1fr))">{logos}</div>'
                    if logos else "") + "</div>")
        if it.get("strike_at") is not None:
            x, y = _xy(p, it, w)
            B.append(f'<div class="strike" id="{iid}-strike" style="left:{_f((x or 0) + 24)}px;'
                     f'top:{_f(y + _px(it.get("strike_y"), 120))}px;width:{_f(w - 48)}px;rotate:-6deg"></div>')
        p.enter(f"#{iid}", at, it.get("from", "up"), 0.55, "back")
    elif typ == "badge":
        B.append(f'<div class="it badge" id="{iid}" data-float="1" {tt} style="{_pos(p, it)}">{esc(it.get("text"))}</div>')
        p.enter(f"#{iid}", at, "pop", 0.45, "back3")
    elif typ == "stamp":
        B.append(f'<div class="it stamp" id="{iid}" {tt} style="{_pos(p, it)}">{esc(it.get("text"))}</div>')
        p.enter(f"#{iid}", at, "stamp", 0.32, "out4")
    elif typ == "line":
        size = f"font-size:{_f(_px(it['size']))}px;" if it.get("size") else ""
        color = f"color:{esc(it['color'])};" if it.get("color") else ""
        font = f"font-family:var(--f-heavy);" if it.get("heavy") else ""
        track = f"letter-spacing:{esc(it['tracking'])};" if it.get("tracking") else ""
        align = "text-align:center;" if it.get("x") == "center" else ""
        B.append(f'<div class="it line" id="{iid}" {tt} style="{_pos(p, it)}{size}{color}{font}{track}{align}">'
                 f'{rich(it.get("text"))}</div>')
        p.enter(f"#{iid}", at, "up", 0.3, "out2")
    elif typ == "big":
        size = _px(it.get("size"), 200)
        color = f"color:{esc(it['color'])};" if it.get("color") else ""
        w = _px(it.get("w"), 0) or None
        if it.get("value") is not None:
            v = _px(it["value"])
            shown = p.num(0 if it.get("count", True) else v, it.get("suffix", ""), it.get("prefix", ""),
                          int(it.get("decimals") or 0))
            if it.get("count", True):
                p.counter(f"#{iid}-v", v, at, _px(it.get("count"), 0.8) if it.get("count") not in (True, None) else 0.8,
                          it.get("suffix", ""), it.get("prefix", ""), int(it.get("decimals") or 0))
        else:
            shown = str(it.get("text") or "")
        ring = f'<span class="ring" id="{iid}-ring"></span>' if it.get("circle_at") is not None else ""
        lab = f'<div class="lab" {tt}>{esc(it["label"])}</div>' if it.get("label") else ""
        style = _pos(p, {**it, "x": None if it.get("x") in (None, "center") else it["x"]}, w)
        B.append(f'<div class="it big" id="{iid}" style="{style}font-size:{_f(size)}px;{color}">'
                 f'<span class="v"><span id="{iid}-v" {tt}>{esc(shown)}</span>{ring}</span>{lab}</div>')
        p.enter(f"#{iid}", at, "far", 0.4, "back2")
    elif typ == "check":
        w = _px(it.get("w"), 960)
        ul = f'<span class="ul" id="{iid}-ul"></span>' if it.get("underline_at") is not None else ""
        B.append(f'<div class="it check" id="{iid}" style="{_pos(p, it, w)}"><span class="n">{esc(it.get("n"))}</span>'
                 f'<span {tt}>{rich(it.get("text"))}</span><span class="ck" id="{iid}-ck">✓</span>{ul}</div>')
        p.enter(f"#{iid}", at, "left", 0.45, "back")
        ck = t(it.get("check_at", it.get("at", 0)))
        p.anim(f"#{iid}-ck", [{"scale": "0"}, {"scale": "1"}], ck, 0.35, "back3")
        if it.get("check_at") is not None:
            p.event(ck, "check")
    elif typ == "image":
        w = _px(it.get("w"), p.w - 2 * _px(it.get("x"), 40))
        hls = "".join(f'<div class="hl" id="{iid}-hl{k}" style="left:{_f(h[0])}px;top:{_f(h[1])}px;'
                      f'width:{_f(h[2])}px;height:{_f(h[3])}px"></div>' for k, h in enumerate(it.get("highlights") or []))
        B.append(f'<div class="it shot" id="{iid}" style="{_pos(p, it, w)}"><div class="in" id="{iid}-in" '
                 f'style="transform-origin:{esc(it.get("origin", "50% 50%"))}"><img src="{esc(it.get("src"))}" alt="">'
                 f'{hls}</div></div>')
        p.enter(f"#{iid}", at, it.get("from", "up"), 0.45, "out")
        if it.get("zoom_at") is not None:
            za = t(it["zoom_at"])
            p.anim(f"#{iid}-in", [{"scale": "1"}, {"scale": str(it.get("zoom", 1.8))}], za, 0.7, "inout", fill="forwards")
            p.event(za, "zoom")
        for k, _ in enumerate(it.get("highlights") or []):
            ha = t(it.get("highlight_at", it.get("at", 0)))
            p.anim(f"#{iid}-hl{k}", [{"scale": "0 1"}, {"scale": "1 1"}], ha, 0.35, "out")
            p.event(ha, "highlight")
    elif typ == "capture":
        w = _px(it.get("w"), 940)
        h = _px(it.get("h"), 560)
        hl = it.get("highlight")
        hl_html = (f'<div class="hl" id="{iid}-hl" style="position:absolute;left:{_f(hl[0])}px;top:{_f(hl[1])}px;'
                   f'width:{_f(hl[2])}px;height:{_f(hl[3])}px;border:4px solid var(--accent);border-radius:6px;'
                   f'background:color-mix(in srgb, var(--accent) 30%, transparent);transform-origin:0 50%"></div>'
                   if hl else "")
        B.append(f'<div class="it browser" id="{iid}" style="{_pos(p, {**it, "h": None}, w)}">'
                 f'<div class="top"><i></i><i></i><i></i><span {tt}>{esc(it.get("url"))}</span></div>'
                 f'<div class="win" style="height:{_f(h)}px"><div id="{iid}-pan"><img src="{esc(it.get("src"))}" alt="">'
                 f'{hl_html}</div></div></div>')
        p.enter(f"#{iid}", at, "up", 0.42, "back2")
        if it.get("pan"):
            p.anim(f"#{iid}-pan", [{"translate": "0 0"}, {"translate": f"0 -{_f(_px(it['pan']))}px"}],
                   at + 0.4, max(1.0, _px(it.get("pan_dur"), 3.0)), "inout", fill="forwards")
        if hl:
            ha = t(it.get("highlight_at", it.get("at", 0))) + (0 if it.get("highlight_at") is not None else 1.0)
            p.anim(f"#{iid}-hl", [{"scale": "0 1"}, {"scale": "1 1"}], ha, 0.5, "out2")
            p.event(ha, "highlight")
    elif typ == "logo":
        w = _px(it.get("w"), 600)
        B.append(f'<div class="it logo" id="{iid}" style="{_pos(p, it, w)}"><img src="{esc(it.get("src"))}" alt=""></div>')
        p.enter(f"#{iid}", at, "far", 0.45, "back")
    elif typ == "photo":
        w, h = _px(it.get("w"), 700), _px(it.get("h"), 900)
        rot = _px(it.get("rotate"))
        B.append(f'<div class="it photo" id="{iid}" style="{_pos(p, {**it, "h": h}, w)}rotate:{_f(rot)}deg">'
                 f'<img id="{iid}-img" src="{esc(it.get("src"))}" alt="" style="object-position:{esc(it.get("pos", "50% 50%"))}"></div>')
        p.anim(f"#{iid}", [{"opacity": 0, "translate": "0 120px", "rotate": f"{_f(rot - 6)}deg"},
                           {"opacity": 1, "translate": "0 0", "rotate": f"{_f(rot)}deg"}], at, 0.6, "back")
        if it.get("drift"):
            p.anim(f"#{iid}-img", [{"scale": "1"}, {"scale": "1.08"}], 0, 30, "linear")
    elif typ == "chevrons":
        n = int(it.get("n") or 3)
        chs = "".join(f'<span class="chev" id="{iid}-c{k}">⌄</span>' for k in range(n))
        B.append(f'<div class="it" id="{iid}" data-float="1" style="left:0;right:0;top:{_f(_px(it.get("y"), 1520))}px">{chs}</div>')
        for k in range(n):
            p.anim(f"#{iid}-c{k}", [{"opacity": 0, "translate": "0 -30px"}, {"opacity": 1, "translate": "0 0"}],
                   at + 0.14 * k, 0.28, "out2")
        # va-et-vient jusqu'à la fin de la scène
        p.anim(f"#{iid}", [{"translate": "0 0"}, {"translate": "0 22px"}], at + 0.5, 0.5, "sine", fill="forwards")
        p.anims[-1].update(i=1000, dir="alternate")
    elif typ == "glass":
        w = _px(it.get("w"), 520)
        ico = f'<div class="ico">{esc(it["icon"])}</div>' if it.get("icon") else ""
        ttl = f'<div class="ttl" {tt}>{rich(it["title"])}</div>' if it.get("title") else ""
        nb = ""
        if it.get("count") is not None and it.get("value") is not None:
            nb = f'<div class="nb" id="{iid}-n" {tt}>{esc(p.num(0, it.get("suffix", ""), it.get("prefix", "")))}</div>'
            p.counter(f"#{iid}-n", _px(it["value"]), at + 0.2, _px(it.get("count"), 0.9), it.get("suffix", ""),
                      it.get("prefix", ""))
        elif it.get("number"):
            nb = f'<div class="nb" {tt}>{esc(it["number"])}</div>'
        sub = f'<div class="sub" {tt}>{esc(it["sub"])}</div>' if it.get("sub") else ""
        bars = ('<div class="bars">' + "".join(f'<i id="{iid}-b{k}" style="height:{_f(_px(v))}%"></i>'
                                               for k, v in enumerate(it.get("bars") or [])) + "</div>") if it.get("bars") else ""
        pic = f'<div class="pic"><img src="{esc(it["shot"])}" alt=""></div>' if it.get("shot") else ""
        B.append(f'<div class="it glass{" dark" if it.get("dark") else ""}" id="{iid}" style="{_pos(p, it, w)}">'
                 f'{ico}{ttl}{nb}{sub}{bars}{pic}</div>')
        p.enter(f"#{iid}", at, it.get("from", "up"), 0.55, "back")
        for k in range(len(it.get("bars") or [])):
            p.anim(f"#{iid}-b{k}", [{"scale": "1 0"}, {"scale": "1 1"}], at + 0.25 + 0.08 * k, 0.45, "back")
    elif typ == "statement":
        w = _px(it.get("w"), 860)
        parts = [(k, cls, html_) for k, cls, html_ in (
            ("k", "kick", esc(it.get("kicker")) if it.get("kicker") else ""),
            ("t", "big", rich(it.get("text")) if it.get("text") else ""),
            ("s", "s2", rich(it.get("sub")) if it.get("sub") else "")) if html_]
        align = f"text-align:{esc(it['align'])};" if it.get("align") else ""
        B.append(f'<div class="it statement" id="{iid}" style="{_pos(p, it, w)}{align}">'
                 + "".join(f'<div class="{cls}" id="{iid}-{k}" {tt}>{h_}</div>' for k, cls, h_ in parts) + "</div>")
        for n, (k, _, _) in enumerate(parts):
            p.anim(f"#{iid}-{k}", [{"opacity": 0, "translate": "0 42px"}, {"opacity": 1, "translate": "0 0"}],
                   at + 0.12 * n, 0.5, "out")
    elif typ == "pill":
        arw = f'<span class="arw">{esc(it.get("arrow", "↗"))}</span>' if it.get("arrow", True) else ""
        B.append(f'<div class="it pill" id="{iid}" {tt} style="{_pos(p, it)}">{rich(it.get("text"))}{arw}</div>')
        p.enter(f"#{iid}", at, "up", 0.45, "back2")
    elif typ == "tag":
        mark = f'<img src="{esc(it["logo"])}" alt="">' if it.get("logo") else '<span class="dot"></span>'
        B.append(f'<div class="it tag" id="{iid}" {tt} style="{_pos(p, it)}">{mark}{esc(it.get("text"))}</div>')
        p.enter(f"#{iid}", at, "up", 0.4, "back2")
    elif typ == "logocard":
        w = f"width:{_f(_px(it['w']))}px;" if it.get("w") else ""
        chip = f'<span class="ch">{esc(it["chip"])}</span>' if it.get("chip") else ""
        badge = f'<span class="bd" id="{iid}-bd">{esc(it["badge"])}</span>' if it.get("badge") else ""
        B.append(f'<div class="it lcard" id="{iid}" style="{_pos(p, it)}{w}"><span class="mk" id="{iid}-mk">'
                 f'<img src="{esc(it.get("logo"))}" alt=""></span><span><div class="nm" {tt}>{esc(it.get("title"))}</div>'
                 f'<div class="rw">{chip}{badge}</div></span></div>')
        p.enter(f"#{iid}", at, "up", 0.5, "back")
        p.anim(f"#{iid}-mk", [{"opacity": 0, "scale": "0.45", "rotate": "-14deg"}, {"opacity": 1, "scale": "1", "rotate": "0deg"}],
               at + 0.12, 0.45, "back3")
        if badge:
            p.anim(f"#{iid}-bd", [{"opacity": 0, "scale": "0"}, {"opacity": 1, "scale": "1"}], at + 0.42, 0.4, "back3")
    elif typ == "cascade":
        cw, ch = _px(it.get("cw"), 330), _px(it.get("ch"), 586)
        step, tilt, rise = _px(it.get("step"), 250), _px(it.get("tilt"), 24), _px(it.get("rise"), 26)
        srcs = list(it.get("src") or [])
        cards = "".join(f'<div class="cd" id="{iid}-c{k}" style="left:{_f(k * step)}px;top:{_f(k * rise)}px;width:{_f(cw)}px;'
                        f'height:{_f(ch)}px;border-radius:{_f(_px(it.get("radius"), 24))}px;transform:rotateY({_f(tilt)}deg);'
                        f'z-index:{len(srcs) - k}"><img src="{esc(s)}" alt=""></div>' for k, s in enumerate(srcs))
        B.append(f'<div class="it cascade" id="{iid}" style="{_pos(p, it)}width:{_f((len(srcs) - 1) * step + cw)}px;'
                 f'height:{_f(ch + (len(srcs) - 1) * rise)}px">{cards}</div>')
        for k in range(len(srcs)):
            p.anim(f"#{iid}-c{k}", [{"opacity": 0, "translate": "170px 0"}, {"opacity": 1, "translate": "0 0"}],
                   at + 0.1 * k, 0.6, "out")
    elif typ == "kicker":
        w = _px(it.get("w"), p.w - 140)
        B.append(f'<div class="it kicker" id="{iid}" style="{_pos(p, {**it, "x": it.get("x", 70)}, w)}">'
                 f'<span {tt}>{esc(it.get("left") or it.get("text"))}</span><span>{esc(it.get("right"))}</span></div>')
        p.enter(f"#{iid}", at, "up", 0.3, "steps")
    elif typ == "headline":
        w = _px(it.get("w"), p.w - 140)
        size = f"font-size:{_f(_px(it['size']))}px;" if it.get("size") else ""
        B.append(f'<div class="it headline" id="{iid}" {tt} style="{_pos(p, {**it, "x": it.get("x", 70)}, w)}{size}">'
                 f'{rich(it.get("text"))}</div>')
        p.anim(f"#{iid}", [{"opacity": 0, "translate": "0 40px", "scale": "0.94"}, {"opacity": 1, "translate": "0 0", "scale": "1"}],
               at, 0.42, "back2")
    elif typ == "sub":
        w = _px(it.get("w"), p.w - 140)
        B.append(f'<div class="it sub" id="{iid}" {tt} style="{_pos(p, {**it, "x": it.get("x", 74)}, w)}">{rich(it.get("text"))}</div>')
        p.enter(f"#{iid}", at, "left", 0.3, "steps")
    elif typ == "note":
        rot = _px(it.get("rotate"), -6)
        B.append(f'<div class="it note" id="{iid}" data-float="1" {tt} style="{_pos(p, it)}rotate:{_f(rot)}deg">'
                 f'{esc(it.get("text"))}</div>')
        p.enter(f"#{iid}", at, "pop", 0.36, "back2")
    elif typ == "rows":
        w = _px(it.get("w"), 940)
        rows = []
        for k, row in enumerate(it.get("rows") or [], 1):
            row = list(row) + [None] * 4
            state = row[3]
            mark = ('<span class="ok" id="%s-ok%d">✓</span>' % (iid, k)) if state is True else \
                   ('<span class="todo"></span>' if state is False else "")
            meta = f'<span class="meta">{esc(row[2])}</span>' if row[2] else ""
            rows.append(f'<div class="row" id="{iid}-r{k}"><span><span class="n">{esc(row[0])}</span>'
                        f'<span {tt}>{rich(row[1])}</span></span><span>{meta}{mark}</span></div>')
        ttl = f'<div class="t">{esc(it["title"])}</div>' if it.get("title") else ""
        B.append(f'<div class="it card rows" id="{iid}" style="{_pos(p, {**it, "x": it.get("x", 70)}, w)}">{ttl}{"".join(rows)}</div>')
        p.anim(f"#{iid}", [{"opacity": 0, "translate": "0 90px", "rotate": "2deg"}, {"opacity": 1, "translate": "0 0", "rotate": "-1.4deg"}],
               at, 0.42, "back2")
        for k in range(1, len(rows) + 1):
            p.anim(f"#{iid}-r{k}", [{"opacity": 0, "translate": "-30px 0"}, {"opacity": 1, "translate": "0 0"}],
                   at + 0.3 + 0.12 * k, 0.3, "steps")
            if f'{iid}-ok{k}"' in rows[k - 1]:
                p.anim(f"#{iid}-ok{k}", [{"scale": "0"}, {"scale": "1"}], at + 0.55 + 0.12 * k, 0.32, "back3")
    elif typ == "mega":
        lines = [str(x) for x in it.get("lines") or [it.get("text") or ""]]
        plain = [re.sub(r"</?em>", "", x) for x in lines]
        w = _px(it.get("w"), p.w - 140)
        size = min(_px(it.get("size"), 200), w / (0.8 * max(1, max(len(x) for x in plain))))
        ring = ""
        if it.get("circle"):
            n = len(plain)
            last = plain[-1]
            ring = (f'<div class="ring" id="{iid}-ring" style="position:absolute;inset:auto;left:-30px;'
                    f'top:{_f(0.86 * size * (n - 1) - 24)}px;width:{_f(min(w, 0.8 * size * len(last) + 90))}px;'
                    f'height:{_f(0.86 * size + 56)}px"></div>')
        B.append(f'<div class="it mega" id="{iid}" {tt} style="{_pos(p, {**it, "x": it.get("x", 70)}, w)}font-size:{_f(size)}px">'
                 + "".join(f'<span class="l">{rich(x)}</span>' for x in lines) + ring + "</div>")
        p.anim(f"#{iid}", [{"opacity": 0, "scale": "0.7", "translate": "0 60px"}, {"opacity": 1, "scale": "1", "translate": "0 0"}],
               at, 0.42, "back2")
        if it.get("circle"):
            ca = t(it.get("circle_at", it.get("at", 0))) + (0 if it.get("circle_at") is not None else 0.6)
            p.anim(f"#{iid}-ring", [{"opacity": 0, "scale": "0.2", "rotate": "-30deg"}, {"opacity": 1, "scale": "1", "rotate": "-4deg"}],
                   ca, 0.36, "back2")
            p.event(ca, "circle")
    elif typ == "keyword":
        w = _px(it.get("w"), p.w - 140)
        B.append(f'<div class="it card kw" id="{iid}" style="{_pos(p, {**it, "x": it.get("x", 70)}, w)}">'
                 f'<div class="kicker"><span {tt}>{esc(it.get("kicker"))}</span></div>'
                 f'<div class="word" id="{iid}-w" {tt}>{rich(it.get("text"))}</div></div>')
        p.anim(f"#{iid}", [{"opacity": 0, "translate": "0 120px", "rotate": "3deg"}, {"opacity": 1, "translate": "0 0", "rotate": "0deg"}],
               at, 0.42, "back2")
        p.anim(f"#{iid}-w em", [{"scale": "0.3"}, {"scale": "1"}], at + 0.5, 0.4, "back3")
    elif typ == "text":           # texte posé sur le visage plein cadre (layout « face »)
        size = _px(it.get("size"), 120)
        col = {"accent": "var(--accent)", "red": "var(--accent)", "grey": "#D9D5CF", "white": "#fff"}.get(
            str(it.get("color") or "white"), esc(it.get("color") or "#fff"))
        extra = f"font-size:{_f(size)}px;color:{col};"
        if it.get("italic"):
            extra += "font-style:italic;"
        if it.get("serif"):
            extra += "font-family:var(--f-serif);"
        if it.get("tracking"):
            extra += f"letter-spacing:{esc(it['tracking'])};"
        align = str(it.get("align") or "center")
        deco = ""
        if it.get("strike_at") is not None:
            deco += f'<span class="strike" id="{iid}-strike"></span>'
        if it.get("circle_at") is not None:
            deco += f'<span class="ring" id="{iid}-ring"></span>'
        if it.get("underline_at") is not None:
            deco += f'<span class="ul" id="{iid}-ul"></span>'
        ck = '<span class="ck">✓</span>' if it.get("check") else ""
        lab = f'<span class="lab">{esc(it["label"])}</span>' if it.get("label") else ""
        left = f"padding-left:{_f(_px(it['left']))}px;" if it.get("left") else ""
        B.append(f'<div class="it ov" id="{iid}" style="left:0;right:0;top:{_f(_px(it.get("y"), 300))}px;'
                 f'text-align:{esc(align)};{extra}{left}"><span class="w" {tt}>{ck}{rich(it.get("text"))}{deco}</span>{lab}</div>')
        if str(it.get("pop") or "scale") == "scale":
            p.anim(f"#{iid}", [{"opacity": 0, "scale": "0.6"}, {"opacity": 1, "scale": "1"}], at, 0.32, "back2")
        else:
            p.anim(f"#{iid}", [{"opacity": 0, "translate": "0 24px"}, {"opacity": 1, "translate": "0 0"}], at, 0.28, "out")
    else:
        raise ValueError(f"unknown item type {typ!r}")
    _decor(p, iid, it, t)
