"""C2PA "content credentials": the provenance manifest some tools write into images.

Where it lives: PNG chunk caBX, JPEG APP11 segments ("JP" JUMBF, possibly split), WebP chunk C2PA. The
manifest store is a tree of JUMBF boxes (ISO/IEC 19566-5) whose assertions are CBOR (RFC 8949) or JSON.
This module decodes the boxes and the CBOR itself (no dependency) and reports what the manifest DECLARES:
the generator, the actions (created, edited, converted...), the software agent and the IPTC digital source
type, e.g. trainedAlgorithmicMedia = made by generative AI.

Not done: the cryptographic signature is NOT verified. A manifest can be stripped (absence proves nothing)
and, without verification, a declaration is a strong hint, not proof.
"""
from __future__ import annotations

import json
import struct

# IPTC NewsCodes "digitalsourcetype" values that mean generative AI or synthetic content
AI_SOURCE_TYPES = {
    "trainedAlgorithmicMedia": "created by a generative AI model (trained on sampled content)",
    "compositeWithTrainedAlgorithmicMedia": "a composite that includes generative AI content",
    "algorithmicMedia": "created purely by an algorithm (not from a capture)",
    "compositeSynthetic": "a composite of synthetic elements",
}
CAPTURE_SOURCE_TYPES = {"digitalCapture", "negativeFilm", "positiveFilm", "print", "minorHumanEdits",
                        "compositeCapture", "algorithmicallyEnhanced", "screenCapture"}


# ---------------------------------------------------------------------------------------------- CBOR

class _Break(Exception):
    pass


def cbor_loads(data):
    value, _ = _cbor(memoryview(data), 0)
    return value


def _cbor(b, i):
    ib = b[i]
    i += 1
    major, info = ib >> 5, ib & 0x1F
    if ib == 0xFF:
        raise _Break(i)

    def length(i):
        if info < 24:
            return info, i
        n = {24: 1, 25: 2, 26: 4, 27: 8}.get(info)
        if n is None:
            return None, i  # indefinite (31)
        return int.from_bytes(b[i:i + n], "big"), i + n
    if major == 7:
        if info == 20:
            return False, i
        if info == 21:
            return True, i
        if info in (22, 23):
            return None, i
        if info == 25:
            return _half(int.from_bytes(b[i:i + 2], "big")), i + 2
        if info == 26:
            return struct.unpack(">f", b[i:i + 4])[0], i + 4
        if info == 27:
            return struct.unpack(">d", b[i:i + 8])[0], i + 8
        return None, i + {24: 1}.get(info, 0)
    n, i = length(i)
    if major == 0:
        return n, i
    if major == 1:
        return -1 - n, i
    if major in (2, 3):
        if n is None:  # indefinite: chunks until break
            parts = []
            while b[i] != 0xFF:
                v, i = _cbor(b, i)
                parts.append(v)
            i += 1
            return (b"".join(parts) if major == 2 else "".join(parts)), i
        raw = bytes(b[i:i + n])
        return (raw if major == 2 else raw.decode("utf-8", "replace")), i + n
    if major == 4:
        out = []
        if n is None:
            while b[i] != 0xFF:
                v, i = _cbor(b, i)
                out.append(v)
            return out, i + 1
        for _ in range(n):
            v, i = _cbor(b, i)
            out.append(v)
        return out, i
    if major == 5:
        out = {}
        count = n
        while count is None or count > 0:
            if count is None and b[i] == 0xFF:
                return out, i + 1
            k, i = _cbor(b, i)
            v, i = _cbor(b, i)
            out[k if isinstance(k, (str, int, float, bool)) else str(k)] = v
            if count is not None:
                count -= 1
        return out, i
    if major == 6:  # tag: keep the tagged value
        return _cbor(b, i)
    raise ValueError(f"bad CBOR major type {major}")


def _half(h):
    s, e, f = h >> 15, (h >> 10) & 0x1F, h & 0x3FF
    v = (f / 1024) * 2 ** -14 if e == 0 else (float("inf") if e == 31 else (1 + f / 1024) * 2 ** (e - 15))
    return -v if s else v


# ---------------------------------------------------------------------------------------------- JUMBF

def _boxes(data, start=0, end=None):
    end = len(data) if end is None else end
    i = start
    while i + 8 <= end:
        lbox, tbox = struct.unpack(">I4s", data[i:i + 8])
        hdr = 8
        if lbox == 1:
            lbox = struct.unpack(">Q", data[i + 8:i + 16])[0]
            hdr = 16
        elif lbox == 0:
            lbox = end - i
        if lbox < hdr or i + lbox > end:
            break
        yield tbox.decode("latin-1"), i + hdr, i + lbox
        i += lbox


def parse_jumbf(data):
    """Tree of JUMBF superboxes: [{'label', 'content': [...], 'children': [...]}]."""
    out = []
    for t, s, e in _boxes(data):
        if t == "jumb":
            out.append(_superbox(data, s, e))
    return out


def _superbox(data, s, e):
    node = {"label": None, "content": [], "children": []}
    for t, cs, ce in _boxes(data, s, e):
        if t == "jumd":
            d = data[cs:ce]
            if len(d) >= 17:
                node["type"] = d[:16].hex()
                toggles = d[16]
                if toggles & 0x02:
                    lab = d[17:].split(b"\x00", 1)[0]
                    node["label"] = lab.decode("utf-8", "replace")
        elif t == "jumb":
            node["children"].append(_superbox(data, cs, ce))
        elif t == "cbor":
            try:
                node["content"].append(cbor_loads(data[cs:ce]))
            except (ValueError, IndexError, _Break, struct.error):
                node["content"].append({"undecodable_cbor_bytes": ce - cs})
        elif t == "json":
            try:
                node["content"].append(json.loads(data[cs:ce]))
            except ValueError:
                node["content"].append({"undecodable_json_bytes": ce - cs})
        else:
            node["content"].append({"box": t, "bytes": ce - cs})
    return node


def jpeg_app11_payloads_to_jumbf(payloads):
    """Reassemble JUMBF from JPEG APP11 'JP' segments (each: CI 'JP', En, Z, then LBox/TBox [XLBox] + data)."""
    groups = {}
    for p in payloads:
        if len(p) < 16 or p[:2] != b"JP":
            continue
        en, z = struct.unpack(">HI", p[2:8])
        groups.setdefault(en, []).append((z, p[8:]))
    out = b""
    for en in sorted(groups):
        parts = sorted(groups[en])
        first = parts[0][1]
        hdr = 16 if struct.unpack(">I", first[:4])[0] == 1 else 8
        out += first + b"".join(x[hdr:] for _, x in parts[1:])
    return out


# ---------------------------------------------------------------------------------------------- summary

def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n["children"])


def _short(v):
    return str(v).rstrip("/").rsplit("/", 1)[-1] if v else v


def summarize(tree):
    """What the manifest declares, flattened for people (and for the scan)."""
    nodes = list(_walk(tree))
    manifests = [n for n in nodes if (n["label"] or "").startswith("urn:")]
    actions, claim, generator, title, sig = [], {}, None, None, False
    for n in nodes:
        label = n["label"] or ""
        for c in n["content"]:
            if not isinstance(c, dict):
                continue
            if label.startswith("c2pa.actions"):
                for act in c.get("actions", []) or []:
                    if isinstance(act, dict):
                        agent = act.get("softwareAgent")
                        if isinstance(agent, dict):
                            agent = " ".join(str(agent.get(k)) for k in ("name", "version") if agent.get(k))
                        actions.append({"action": act.get("action"), "when": act.get("when"),
                                        "software_agent": agent,
                                        "digital_source_type": _short(act.get("digitalSourceType"))})
            if label.startswith("c2pa.claim"):
                claim = c
                info = c.get("claim_generator_info")
                if isinstance(info, list) and info and isinstance(info[0], dict):
                    generator = info[0].get("name")
                elif isinstance(info, dict):
                    generator = info.get("name")
                generator = generator or c.get("claim_generator")
                title = c.get("dc:title") or title
        if label.startswith("c2pa.signature"):
            sig = True
    types = [a["digital_source_type"] for a in actions if a["digital_source_type"]]
    ai = [t for t in types if t in AI_SOURCE_TYPES]
    out = {"present": True, "manifests": len(manifests), "generator": generator, "title": title,
           "instance_id": claim.get("instanceID"), "actions": actions, "digital_source_types": sorted(set(types)),
           "signature": "present, NOT verified by this tool" if sig else "not found",
           "ai_generated": bool(ai)}
    if ai:
        agents = sorted({a["software_agent"] for a in actions if a["software_agent"]})
        out["meaning"] = (f"The file's own C2PA manifest declares {ai[0]}: {AI_SOURCE_TYPES[ai[0]]}"
                          + (f" (software: {', '.join(agents)})" if agents else "") +
                          ". The picture may show no real place. The signature was not verified.")
    elif types and set(types) <= CAPTURE_SOURCE_TYPES:
        out["meaning"] = f"The manifest declares a capture ({', '.join(sorted(set(types)))}); signature not verified."
    return out


def parse(data):
    """Summary of a C2PA manifest store given as raw JUMBF bytes; {'present': False} when nothing decodes."""
    try:
        tree = parse_jumbf(bytes(data))
    except (struct.error, ValueError, IndexError):
        tree = []
    if not tree:
        return {"present": False}
    return summarize(tree)
