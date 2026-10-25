#!/usr/bin/env python3
"""Diagnostica dados públicos da apuração Probitas sem inventar resultados.

Usa um navegador real para registrar as requisições JSON/HTML. Apenas publica
resultados quando encontra explicitamente votos e seções, com validação.
"""
import asyncio
import datetime as dt
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright

BASE = "https://probitasbrasil.org/apuracao/"
OUT = Path("data/exterior.json")
REPORT = Path("data/probitas_diagnostico.json")
NUMBER = re.compile(r"^\d{1,3}(?:\.\d{3})*$|^\d+$")

def integer(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and NUMBER.fullmatch(value.strip()):
        return int(value.replace(".", ""))
    return None

def scan(value, trail="", depth=0, results=None):
    if results is None:
        results = []
    if depth > 13:
        return results
    if isinstance(value, dict):
        # Guardar APENAS indícios: não inferir automaticamente que contagens
        # com nomes parecidos são votos do segundo turno.
        keys = [str(k) for k in value]
        interesting = [k for k in keys if re.search(
            r"lula|fl[aá]vio|bolsonaro|se[cç][oõ]es|sections|votos|votes|apurad|totaliz",
            k, re.I)]
        if interesting:
            results.append({"path": trail or "$", "keys": interesting[:25]})
        for k, v in value.items():
            scan(v, trail + "/" + str(k), depth + 1, results)
    elif isinstance(value, list):
        for i, v in enumerate(value[:100]):
            scan(v, trail + "/" + str(i), depth + 1, results)
    return results

def extract_explicit(data):
    """Somente esquema nominal inequívoco, sem interpretar métricas desconhecidas."""
    if not isinstance(data, dict):
        return None
    aliases = {
        "lula_votes": ("lula_votes", "votos_lula", "votosLula"),
        "flavio_votes": ("flavio_votes", "votos_flavio", "votosFlavio"),
        "sections_counted": ("sections_counted", "secoes_apuradas", "secoesApuradas"),
        "sections_total": ("sections_total", "secoes_total", "totalSecoes"),
    }
    matched = {}
    for field, options in aliases.items():
        candidates = [integer(data[k]) for k in options if k in data]
        candidates = [n for n in candidates if n is not None]
        if len(set(candidates)) != 1:
            return None
        matched[field] = candidates[0]
    if matched["lula_votes"] + matched["flavio_votes"] <= 0:
        return None
    if not 0 < matched["sections_total"] <= 100000 or not 0 <= matched["sections_counted"] <= matched["sections_total"]:
        return None
    return matched

async def main():
    captured = []
    selected = None
    page_text = ""
    errors = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(locale="pt-BR", timezone_id="America/Sao_Paulo",
                                      user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36")
        async def on_response(response):
            nonlocal selected
            url = response.url
            if urlparse(url).scheme not in ("https", "http"):
                return
            ctype = response.headers.get("content-type", "")
            if not any(t in ctype for t in ("json", "javascript")):
                return
            try:
                body = await response.body()
                if len(body) > 5_000_000:
                    return
                data = json.loads(body)
                findings = scan(data)[:35]
                entry = {"url":url.split("?")[0], "status":response.status,
                         "content_type":ctype, "indicios":findings}
                if findings:
                    captured.append(entry)
                if selected is None:
                    candidate = extract_explicit(data)
                    if candidate and "probitasbrasil.org" in (urlparse(url).hostname or ""):
                        selected = candidate
            except (ValueError, UnicodeDecodeError, RuntimeError):
                return
            except Exception as exc:
                errors.append(str(exc)[:150])
        page.on("response", on_response)
        try:
            await page.goto(BASE, wait_until="domcontentloaded", timeout=35000)
            await page.wait_for_timeout(15000)
            page_text = (await page.locator("body").inner_text(timeout=5000))[:20000]
        except Exception as exc:
            errors.append("Página: " + str(exc)[:180])
        await browser.close()

    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    # Não publicar texto bruto nem resultados parcialmente identificados:
    # podem ser do primeiro turno, Brasil em vez de exterior ou projeções.
    report = {"checked_at":timestamp, "source":BASE, "status":"validado" if selected else "requer_mapeamento",
              "responses_with_indicators":captured[:80], "page_indicators":
              sorted(set(re.findall(r"(?i)lula|fl[aá]vio|se[cç][oõ]es|exterior|estados unidos|portugal|jap[aã]o", page_text)))[:30],
              "errors":errors[:10]}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if selected:
        payload = {**selected, "validated":True,"source":BASE,"collected_at":timestamp,
                   "countries":[]}
        OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print("VALIDADO: votos e seções gravados em data/exterior.json")
    else:
        print("SEM DADOS VALIDADOS: consulte data/probitas_diagnostico.json. Não altera os números anteriores.")
    print(f"Requisições com indícios: {len(captured)}; erros: {len(errors)}")

if __name__ == "__main__":
    asyncio.run(main())
