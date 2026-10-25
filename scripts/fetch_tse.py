#!/usr/bin/env python3
"""Consulta os arquivos oficiais EA20 do TSE (2º turno, presidente)."""
import datetime
import json
import os
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
                    number = str(candidate.get("n", ""))
                    if number in ("13", "22"):
                        votes[number] = int(candidate.get("vap") or 0)
    sections = raw.get("s", {})
    return {
        "votes": votes,
        "valid": int(raw.get("v", {}).get("vv") or sum(votes.values())),
        "sections_total": int(sections.get("ts") or 0),
        "sections_done": int(sections.get("st") or 0),
        "published_at": f"{raw.get('dg','')} {raw.get('hg','')}".strip(),
        "generation_id": raw.get("idg"),
    }


def has_results(item):
    return bool(item and (item.get("sections_done", 0) > 0 or
                          sum((item.get("votes") or {}).values()) > 0 or
                          item.get("valid", 0) > 0))


def public_signature(item):
    if item is None:
        return None
    return {
        "votes": item.get("votes"),
        "valid": item.get("valid"),
        "sections_total": item.get("sections_total"),
        "sections_done": item.get("sections_done"),
    }


def main():
    result = {"collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "source": "TSE", "brasil": None, "exterior": None, "ufs": {}, "errors": {}}
    previous = None
    if OUT.exists():
        try:
            candidate = json.loads(OUT.read_text(encoding="utf-8"))
            if candidate.get("source") == "TSE" and isinstance(candidate.get("ufs"), dict):
                previous = candidate
        except (OSError, ValueError):
            previous = None

    for uf in ["br", "zz", *UFS]:
        old = (previous or {}).get("brasil") if uf == "br" else (
            (previous or {}).get("exterior") if uf == "zz" else
            (previous or {}).get("ufs", {}).get(uf.upper()))
        try:
            item = parse(fetch(uf))
            if not has_results(item) and has_results(old):
                result["errors"][uf] = "resposta vazia; dados anteriores válidos preservados"
                item = old
            elif not has_results(item):
                result["errors"][uf] = "resposta sem votos/seções apuradas"
                item = old
            if uf == "br":
                result["brasil"] = item
            elif uf == "zz":
                result["exterior"] = item
            elif item is not None:
                result["ufs"][uf.upper()] = item
        except Exception as exc:
            result["errors"][uf] = str(exc)[:160]
            if uf == "br":
                result["brasil"] = old
            elif uf == "zz":
                result["exterior"] = old
            elif old is not None:
                result["ufs"][uf.upper()] = old
        time.sleep(0.15)

    if not any([result["brasil"], result["exterior"], *result["ufs"].values()]):
        raise RuntimeError("Nenhum arquivo oficial disponível; preservando último JSON")
    if previous:
        changed = any(
            public_signature(result.get(key)) != public_signature(previous.get(key))
            for key in ("brasil", "exterior")
        ) or {
            uf: public_signature(item) for uf, item in result["ufs"].items()
        } != {
            uf: public_signature(item) for uf, item in previous.get("ufs", {}).items()
        }
        if not changed:
            print("Dados e seções do TSE inalterados; mantendo JSON e evitando commit/deploy.")
            if result["errors"]:
                print(f"Fontes com erro/vazias, dados anteriores preservados: {len(result['errors'])}")
            return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUT.with_suffix(OUT.suffix + ".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, OUT)
    print(f"Coletados: {len(result['ufs'])} UFs. Erros: {len(result['errors'])}")


if __name__ == "__main__":
    main()
