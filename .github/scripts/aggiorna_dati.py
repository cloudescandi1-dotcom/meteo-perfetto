"""Scarica le misure degli aeroporti e le allerte ufficiali e le salva in dati/.

Gira ogni 20 minuti con GitHub Actions: i due servizi non permettono al browser
di leggerli direttamente, quindi l'app li legge da qui, dal suo stesso sito.
Usa solo la libreria standard di Python.
"""
import csv
import gzip
import io
import json
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = "MeteoPerfetto/1.0 (https://cloudescandi1-dotcom.github.io/meteo-perfetto/)"
METAR_URL = "https://aviationweather.gov/data/cache/metars.cache.csv.gz"
MA_URL = "https://feeds.meteoalarm.org/api/v1/warnings/feeds-{}"
PAESI = ["austria", "belgium", "bosnia-herzegovina", "bulgaria", "croatia", "cyprus", "czechia",
         "denmark", "estonia", "finland", "france", "germany", "greece", "hungary", "iceland",
         "ireland", "israel", "italy", "latvia", "lithuania", "luxembourg", "malta", "moldova",
         "montenegro", "netherlands", "norway", "poland", "portugal", "republic-of-north-macedonia",
         "romania", "serbia", "slovakia", "slovenia", "spain", "sweden", "switzerland", "ukraine",
         "united-kingdom"]
TIPI = {"1": "vento", "2": "neve e ghiaccio", "3": "temporali", "4": "nebbia", "5": "caldo intenso",
        "6": "freddo intenso", "7": "mareggiate", "8": "incendi boschivi", "9": "valanghe",
        "10": "pioggia", "12": "alluvioni", "13": "pioggia e alluvioni"}


def scarica(url, tentativi=3):
    for k in range(tentativi):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:  # rete instabile: riprova
            print(f"  errore {url}: {e}", file=sys.stderr)
            time.sleep(3 * (k + 1))
    return None


def numero(v, tipo=float):
    try:
        return tipo(float(v))
    except (TypeError, ValueError):
        return None


def stazioni():
    raw = scarica(METAR_URL)
    if not raw:
        return []
    righe = gzip.decompress(raw).decode("utf-8", "replace").splitlines()
    inizio = next(i for i, r in enumerate(righe) if r.startswith("raw_text"))
    lettore = csv.reader(io.StringIO("\n".join(righe[inizio:])))
    intest = next(lettore)
    col = {n: intest.index(n) for n in intest}
    copertura = [i for i, n in enumerate(intest) if n == "sky_cover"]
    ordine = {"SKC": 0, "CLR": 0, "NSC": 0, "NCD": 0, "CAVOK": 0, "FEW": 1, "SCT": 2, "BKN": 3, "OVC": 4, "OVX": 4}
    adesso = time.time()
    out = []
    for r in lettore:
        if len(r) < len(intest):
            continue
        try:
            t = datetime.fromisoformat(r[col["observation_time"]].replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        temp = numero(r[col["temp_c"]])
        lat, lon = numero(r[col["latitude"]]), numero(r[col["longitude"]])
        if temp is None or lat is None or lon is None or adesso - t > 3 * 3600:
            continue
        cop = [ordine[r[i]] for i in copertura if r[i] in ordine]
        # [sigla, lat, lon, quota m, minuto della misura (UTC), temperatura, vento kn, fenomeni, copertura 0-4]
        out.append([
            r[col["station_id"]], round(lat, 3), round(lon, 3), numero(r[col["elevation_m"]], int),
            int(t // 60), temp, numero(r[col["wind_speed_kt"]], int),
            r[col["wx_string"]].strip(), max(cop) if cop else None,
        ])
    return out


def chiave_nome(s):
    """Nome di zona confrontabile: senza accenti, spazi, trattini e congiunzioni."""
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"\b(e|and|et|und|y)\b", " ", s)
    return re.sub(r"[^a-z]", "", s)


def allerte():
    adesso = datetime.now(timezone.utc)
    viste, out = set(), []
    for paese in PAESI:
        raw = scarica(MA_URL.format(paese), tentativi=2)
        if not raw:
            continue
        try:
            dati = json.loads(raw)
        except ValueError:
            continue
        for w in dati.get("warnings", []):
            info = w.get("alert", {}).get("info", [])
            if not info:
                continue
            # Testo in italiano se c'è, altrimenti in inglese
            scelta = next((i for i in info if i.get("language", "").startswith("it")), None) \
                or next((i for i in info if i.get("language", "").startswith("en")), info[0])
            par = {p["valueName"]: p["value"] for p in scelta.get("parameter", [])}
            livello = numero(par.get("awareness_level", "0").split(";")[0], int) or 0
            tipo = par.get("awareness_type", "").split(";")[0].strip()
            try:
                fine = datetime.fromisoformat(scelta["expires"])
                inizio = datetime.fromisoformat(scelta.get("onset") or scelta["effective"])
            except (KeyError, ValueError):
                continue
            if livello < 2 or fine < adesso:
                continue
            aree = [a.get("areaDesc", "") for a in scelta.get("area", [])]
            chiave = (paese, tuple(aree), tipo, livello, inizio.isoformat(), fine.isoformat())
            if chiave in viste:
                continue
            viste.add(chiave)
            testo = re.split(r"\(DISCLAIMER", scelta.get("description", ""), flags=re.I)[0].strip()
            out.append({
                "aree": aree, "n": sorted({chiave_nome(a) for a in aree}),
                "l": livello, "t": TIPI.get(tipo, tipo.lower()),
                "da": inizio.isoformat(), "a": fine.isoformat(), "x": testo[:300],
            })
    return out


def main():
    cartella = Path(sys.argv[1] if len(sys.argv) > 1 else "dati")
    cartella.mkdir(parents=True, exist_ok=True)
    agg = datetime.now(timezone.utc).isoformat(timespec="seconds")
    st = stazioni()
    al = allerte()
    (cartella / "stazioni.json").write_text(json.dumps({"agg": agg, "s": st}, separators=(",", ":")), encoding="utf-8")
    (cartella / "allerte.json").write_text(json.dumps({"agg": agg, "a": al}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"stazioni: {len(st)}, allerte attive (gialle o più): {len(al)}")


if __name__ == "__main__":
    main()
