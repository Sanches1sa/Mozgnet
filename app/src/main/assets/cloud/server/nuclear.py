# -*- coding: utf-8 -*-
"""
nuclear.py — v3.124: УРАВНЕНИЯ ЯДЕРНЫХ РАСПАДОВ (расчёт, не заготовка).

«Бета-распад калия», «покажи формулой» — уравнение реакции считается по
законам сохранения: массовое число A и заряд Z слева и справа равны.
  β⁻: ᴬ_Z X → ᴬ_(Z+1) Y + e⁻ + ν̄ₑ   (нейтрон -> протон)
  β⁺: ᴬ_Z X → ᴬ_(Z−1) Y + e⁺ + νₑ   (протон -> нейтрон)
  ЭЗ: ᴬ_Z X + e⁻ → ᴬ_(Z−1) Y + νₑ   (электронный захват)
  α : ᴬ_Z X → ᴬ⁻⁴_(Z−2) Y + ⁴₂He
Элемент — из вопроса или из прошлой реплики диалога («а формулой это?»).
Массовое число — из вопроса («калий-40», «K-40») или известный
радиоактивный изотоп элемента. Работает без интернета.
"""
import re

_EL = """H водород;He гелий;Li литий;Be бериллий;B бор;C углерод;N азот;O кислород;
F фтор;Ne неон;Na натрий;Mg магний;Al алюминий;Si кремний;P фосфор;S сера;
Cl хлор;Ar аргон;K калий;Ca кальций;Sc скандий;Ti титан;V ванадий;Cr хром;
Mn марганец;Fe железо;Co кобальт;Ni никель;Cu медь;Zn цинк;Ga галлий;
Ge германий;As мышьяк;Se селен;Br бром;Kr криптон;Rb рубидий;Sr стронций;
Y иттрий;Zr цирконий;Nb ниобий;Mo молибден;Tc технеций;Ru рутений;Rh родий;
Pd палладий;Ag серебро;Cd кадмий;In индий;Sn олово;Sb сурьма;Te теллур;
I йод;Xe ксенон;Cs цезий;Ba барий;La лантан;Ce церий;Pr празеодим;Nd неодим;
Pm прометий;Sm самарий;Eu европий;Gd гадолиний;Tb тербий;Dy диспрозий;
Ho гольмий;Er эрбий;Tm тулий;Yb иттербий;Lu лютеций;Hf гафний;Ta тантал;
W вольфрам;Re рений;Os осмий;Ir иридий;Pt платина;Au золото;Hg ртуть;
Tl таллий;Pb свинец;Bi висмут;Po полоний;At астат;Rn радон;Fr франций;
Ra радий;Ac актиний;Th торий;Pa протактиний;U уран;Np нептуний;Pu плутоний;
Am америций;Cm кюрий;Bk берклий;Cf калифорний;Es эйнштейний;Fm фермий"""
ELEMENTS = []                               # индекс = Z - 1: (символ, имя)
for _p in _EL.replace("\n", "").split(";"):
    _s, _n = _p.split()
    ELEMENTS.append((_s, _n))
_SYM = {s: z + 1 for z, (s, _n) in enumerate(ELEMENTS)}

# известные радиоактивные изотопы: символ -> (A, основной распад, заметка)
KNOWN = {"H": (3, "b-", ""), "C": (14, "b-", ""), "N": (13, "b+", ""),
         "O": (15, "b+", ""), "F": (18, "b+", ""), "Na": (22, "b+", ""),
         "P": (32, "b-", ""), "S": (35, "b-", ""), "K": (40, "b-",
         "калий-40: около 89 % распадов — β⁻ (в кальций-40), около 11 % — "
         "электронный захват (в аргон-40; на этом основан калий-аргоновый "
         "метод датирования)"), "Ca": (45, "b-", ""), "Co": (60, "b-", ""),
         "Sr": (90, "b-", ""), "Tc": (99, "b-", ""), "I": (131, "b-", ""),
         "Cs": (137, "b-", ""), "Po": (210, "a", ""), "Rn": (222, "a", ""),
         "Ra": (226, "a", ""), "Th": (232, "a", ""), "U": (238, "a", ""),
         "Pu": (239, "a", ""), "Am": (241, "a", ""), "Bi": (210, "b-", ""),
         "Pb": (210, "b-", ""), "Ni": (63, "b-", ""), "Kr": (85, "b-", ""),
         "Xe": (133, "b-", ""), "Fe": (59, "b-", ""), "Zn": (65, "b+", ""),
         "Cu": (64, "b-", ""), "Ar": (39, "b-", ""), "Cl": (36, "b-", "")}

_FORM_Q = re.compile(r"формул|уравнени|реакци|покаж|запиш|напиш", re.I)
_DECAY = re.compile(r"распад|β|бета|альфа|α|позитрон|электронн\w* захват|"
                    r"[kк]-?захват", re.I)


def _name_re():
    alts = []
    for z, (s, n) in enumerate(ELEMENTS):
        stem = n[:-1] if n[-1] in "йяаоь" and len(n) > 4 else n
        alts.append((stem, z + 1))
    alts.sort(key=lambda x: -len(x[0]))
    return alts


_NAMES = _name_re()


def _mode(t):
    t = str(t or "").lower()
    if re.search(r"позитрон|бета[\s-]*плюс|β\s*\+|β⁺|бета\+", t):
        return "b+"
    if re.search(r"электронн\w* захват|[kк]-?захват", t):
        return "ec"
    if re.search(r"альфа|α", t):
        return "a"
    if re.search(r"бета|β", t):
        return "b-"
    return ""


def find(text):
    """-> {"z", "a", "mode"} | None: элемент (и изотоп) в тексте."""
    t = str(text or "")
    tl = t.lower()
    z = a = None
    m = re.search(r"\b([A-Z][a-z]?)\s*-\s*(\d{1,3})\b|\b(\d{1,3})\s*([A-Z][a-z]?)\b", t)
    if m:
        s = m.group(1) or m.group(4)
        if s in _SYM:
            z, a = _SYM[s], int(m.group(2) or m.group(3))
    if z is None:
        for stem, zz in _NAMES:
            mm = re.search(r"(?<![а-яё])%s[а-яё]{0,3}(?:\s*-?\s*(\d{1,3}))?(?![а-яё])"
                           % re.escape(stem), tl)
            if mm:
                z = zz
                if mm.group(1):
                    a = int(mm.group(1))
                break
    if z is None:
        return None
    mode = _mode(t)
    sym = ELEMENTS[z - 1][0]
    if a is None and sym in KNOWN:
        a = KNOWN[sym][0]
    if not mode and sym in KNOWN:
        mode = KNOWN[sym][1]
    if a is None or not mode:
        return None
    return {"z": z, "a": a, "mode": mode}


def _nuc(a, z):
    s = ELEMENTS[z - 1][0] if 1 <= z <= len(ELEMENTS) else "?"
    return "{}^{%d}_{%d}\\mathrm{%s}" % (a, z, s)


def _gen(n):
    """калий -> калия (родительный, грубо: только для подписи)."""
    if n.endswith("ий"):
        return n[:-2] + "ия"
    if n.endswith("ь"):
        return n[:-1] + "и"
    if n.endswith("а"):
        return n[:-1] + "ы"
    if n.endswith("о"):
        return n[:-1] + "а"
    return n + "а"


def equation(z, a, mode):
    """-> (TeX, z2, a2, пояснение)."""
    if mode == "b-":
        z2, a2 = z + 1, a
        tex = "%s \\to %s + e^{-} + \\bar{\\nu}_e" % (_nuc(a, z), _nuc(a2, z2))
        why = ("β⁻-распад: нейтрон ядра превращается в протон, ядро испускает "
               "электрон и электронное антинейтрино. Заряд Z растёт на 1, "
               "массовое число A не меняется")
    elif mode == "b+":
        z2, a2 = z - 1, a
        tex = "%s \\to %s + e^{+} + \\nu_e" % (_nuc(a, z), _nuc(a2, z2))
        why = ("β⁺-распад: протон ядра превращается в нейтрон, ядро испускает "
               "позитрон и электронное нейтрино. Заряд Z уменьшается на 1, "
               "A не меняется")
    elif mode == "ec":
        z2, a2 = z - 1, a
        tex = "%s + e^{-} \\to %s + \\nu_e" % (_nuc(a, z), _nuc(a2, z2))
        why = ("электронный захват: ядро захватывает электрон оболочки, протон "
               "становится нейтроном, вылетает нейтрино. Z уменьшается на 1, "
               "A не меняется")
    else:
        z2, a2 = z - 2, a - 4
        tex = "%s \\to %s + {}^{4}_{2}\\mathrm{He}" % (_nuc(a, z), _nuc(a2, z2))
        why = ("α-распад: ядро испускает альфа-частицу (ядро гелия-4). "
               "Z уменьшается на 2, A — на 4")
    return tex, z2, a2, why


_GENERAL = {"b-": "X(A,\\ Z) \\to Y(A,\\ Z+1) + e^{-} + \\bar{\\nu}_e",
            "b+": "X(A,\\ Z) \\to Y(A,\\ Z-1) + e^{+} + \\nu_e",
            "ec": "X(A,\\ Z) + e^{-} \\to Y(A,\\ Z-1) + \\nu_e",
            "a": "X(A,\\ Z) \\to Y(A-4,\\ Z-2) + \\mathrm{He}(4,\\ 2)"}


def answer(text, ctx_texts=()):
    """Вопрос (и прошлые реплики) -> текст ответа с формулами | ''.
    Срабатывает на вопрос о распаде элемента или на просьбу «формулой» в
    продолжение такого разговора."""
    t = str(text or "")
    f = find(t) if _DECAY.search(t) or _FORM_Q.search(t) else None
    if f is None and _FORM_Q.search(t):
        for prev in ctx_texts or ():
            if _DECAY.search(str(prev)):
                f = find(str(prev) + " " + t)
                if f:
                    m2 = _mode(t)
                    if m2:
                        f["mode"] = m2
                    break
    if not f:
        return ""
    z, a, mode = f["z"], f["a"], f["mode"]
    sym, name = ELEMENTS[z - 1]
    tex, z2, a2, why = equation(z, a, mode)
    if not (1 <= z2 <= len(ELEMENTS)):
        return ""
    sym2, name2 = ELEMENTS[z2 - 1]
    lines = ["**Уравнение распада**",
             "$%s$" % tex,
             "%s-%d (Z = %d) → %s-%d (Z = %d). %s." % (
                 name.capitalize(), a, z, name2, a2, z2, why),
             "Общий вид (A — массовое число, Z — заряд ядра): $%s$" % _GENERAL[mode],
             "Проверка: слева и справа одинаковы массовое число (%d) и заряд." % a]
    note = KNOWN.get(sym, (0, "", ""))[2]
    if note and KNOWN[sym][0] == a:
        lines.append(note[0].upper() + note[1:] + ".")
        if sym == "K" and a == 40 and mode == "b-":
            tex2, _z, _a, _w = equation(z, a, "ec")
            lines.append("Вторая ветвь (электронный захват): $%s$" % tex2)
    lines.append("_Составлено: расчёт по законам сохранения заряда и "
                 "массового числа (без интернета)_")
    return "\n".join(lines)
