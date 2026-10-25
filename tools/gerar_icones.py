#!/usr/bin/env python3
"""Gera os ícones PNG do app a partir de icons/icone.svg (requer Playwright + Chromium).

  python3 tools/gerar_icones.py

Variantes:
  icon-192/512.png           padrão (cantos arredondados, fundo transparente fora da forma)
  icon-maskable-192/512.png  Android: fundo até a borda e conteúdo na zona segura (80% central)
  apple-touch-icon.png       iPhone/iPad, 180 px, sem transparência (o iOS arredonda sozinho)
  favicon-32.png             aba do navegador
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

PASTA = Path(__file__).resolve().parent.parent / "icons"
BASE = (PASTA / "icone.svg").read_text(encoding="utf-8")


def variante(sangria: bool, escala: float) -> str:
    svg = BASE
    if sangria:  # fundo ocupa o quadrado inteiro
        svg = svg.replace('<rect id="forma" width="512" height="512" rx="112"/>',
                          '<rect id="forma" width="512" height="512"/>')
    if escala != 1:
        svg = svg.replace('<g id="conteudo">',
                          f'<g id="conteudo" transform="translate(256 256) scale({escala}) translate(-256 -256)">')
    return svg


SAIDAS = [
    ("icon-192.png", 192, False, 1), ("icon-512.png", 512, False, 1),
    ("icon-maskable-192.png", 192, True, .82), ("icon-maskable-512.png", 512, True, .82),
    ("apple-touch-icon.png", 180, True, .9), ("favicon-32.png", 32, False, 1),
]

with sync_playwright() as p:
    nav = p.chromium.launch()
    pagina = nav.new_page()
    for nome, lado, sangria, escala in SAIDAS:
        svg = variante(sangria, escala).replace("<svg ", f'<svg width="{lado}" height="{lado}" ', 1)
        pagina.set_viewport_size({"width": lado, "height": lado})
        pagina.set_content(f'<body style="margin:0;background:transparent">{svg}</body>')
        pagina.locator("svg").screenshot(path=str(PASTA / nome), omit_background=not sangria)
        print(f"{nome}: {lado}x{lado}")
    nav.close()
