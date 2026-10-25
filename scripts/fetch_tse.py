#!/usr/bin/env python3
"""Consulta os arquivos oficiais EA20 do TSE (2º turno, presidente)."""
import datetime
import json
import pathlib
import urllib.request
import time

BASE = "https://resultados.tse.jus.br/oficial/ele2026/6258/dados"
UFS = "ac al ap am ba ce df es go ma mt ms mg pa pb pr pe pi rj rn rs ro rr sc sp se to".split()
OUT = pathlib.Path("data/results.json")

def fetch(uf):
    url = f"{BASE}/{uf}/{uf}-c0001-e006258-u.json"
    req = urllib.request.Request(url, headers={"User-Agent": "Eleicoes2026Dashboard/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.load(res)

def parse(raw):
    votes = {}
    for office in raw.get("carg", []):
        if str(office.get("cd")) != "1":
            continue
        for group in office.get("agr", []):
            for party in group.get("par", []):
                for candidate in party.get("cand", []):
                    n = str(candidate.get("n", ""))
                    if n in ("13", "22"):
                        votes[n] = int(candidate.get("vap") or 0)
    sections = raw.get("s", {})
    return {
        "votes": votes,
        "valid": int(raw.get("v", {}).get("vv") or sum(votes.values())),
        "sections_total": int(sections.get("ts") or 0),
        "sections_done": int(sections.get("st") or 0),
        "published_at": f"{raw.get('dg','')} {raw.get('hg','')}".strip(),
        "generation_id": raw.get("idg"),
    }

def main():
    result = {"collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "source": "TSE", "brasil": None, "exterior": None, "ufs": {}, "errors": {}}
    for uf in ["br", "zz", *UFS]:
        try:
            item = parse(fetch(uf))
            if uf == "br":
                result["brasil"] = item
            elif uf == "zz":
                result["exterior"] = item
            else:
                result["ufs"][uf.upper()] = item
        except Exception as exc:
            result["errors"][uf] = str(exc)[:160]
        time.sleep(0.15)
    if not result["brasil"] and not result["ufs"]:
        raise RuntimeError("Nenhum arquivo oficial disponível; preservando último JSON")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        try:
            previous = json.loads(OUT.read_text(encoding="utf-8"))
            if previous.get("brasil") == result["brasil"] and previous.get("exterior") == result["exterior"] and previous.get("ufs") == result["ufs"]:
                print("Votos e seções do TSE inalterados; mantendo JSON e evitando deploy.")
                return
        except (ValueError, OSError):
            pass
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Coletados: {len(result['ufs'])} UFs. Erros: {len(result['errors'])}")

if __name__ == "__main__":
    main()
