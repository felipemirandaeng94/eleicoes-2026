#!/usr/bin/env python3
"""Coletor da apuração do exterior publicada pelo Probitas Brasil.

Fonte mapeada em 25/10/2026 inspecionando a página, a rede e o app.js do site:

  https://probitasbrasil.org/apuracao/state.json
      Retrato JSON do estado da apuração. O próprio app.js do Probitas usa este
      arquivo no modo "?estatico=1" (plano B sem conexão ao vivo). Fonte primária.
  https://probitasbrasil.org/apuracao/events
      Fluxo Server-Sent Events usado pela página ao vivo. O evento "estado"
      carrega o mesmo JSON. Usado apenas como reserva.

O placar "Só exterior" exibido na página corresponde a estado["exterior"]:
  obs.L / obs.F   votos de Lula (13) e Flávio Bolsonaro (22) no 2º turno
                  (contagem não oficial: soma dos boletins de urna já publicados)
  obs.B / obs.N   brancos / nulos       obs.C  comparecimento
  obs.E           eleitores aptos das seções já contadas
  secRec/secTot   seções contadas / total de seções do exterior
  cidades[]       o mesmo detalhamento por cidade (pais, regiao, sec, rec, obs)

Campos que NÃO são votos do 2º turno e nunca são publicados como tal:
  t1, t1oficial, cmp, mesmos  -> 1º turno ou comparação com o 1º turno
  proj                        -> projeção estatística
  pApur                       -> fração do ELEITORADO coberto (não de seções)
  nacional, ufs               -> Brasil, não exterior

Fluxo: Probitas -> validação -> SQLite horário (UTC) -> data/exterior.json.
Em caso de falha, nada é sobrescrito: o último resultado válido é preservado
e o script termina com código 2 para que a execução apareça como falha.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import http.client
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ORIGEM = "https://probitasbrasil.org/apuracao/"
URL_STATE = "https://probitasbrasil.org/apuracao/state.json"
URL_SSE = "https://probitasbrasil.org/apuracao/events"
USER_AGENT = ("Eleicoes2026Painel/2.0 "
              "(+https://github.com/felipemirandaeng94/eleicoes-2026)")
NATUREZA = ("Contagem não oficial do Probitas Brasil: soma dos boletins de urna "
            "do exterior já publicados pelo TSE (2º turno, Presidente).")
CAMPOS_VOTOS = ("L", "F", "B", "N", "O", "C", "E")


class ErroFonte(Exception):
    """Falha de acesso ou formato inesperado na fonte."""


def agora_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(momento: dt.datetime) -> str:
    return momento.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def de_epoch_ms(valor) -> str | None:
    if isinstance(valor, (int, float)) and not isinstance(valor, bool) and valor > 0:
        return iso(dt.datetime.fromtimestamp(valor / 1000, dt.timezone.utc))
    return None


# ---------------------------------------------------------------- acesso à fonte

def baixar_state(tentativas: int = 3, timeout: float = 20.0) -> dict:
    erros = []
    for tentativa in range(1, tentativas + 1):
        req = urllib.request.Request(URL_STATE, headers={
            "User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                tipo = resp.headers.get("content-type", "")
                corpo = resp.read(5_000_000)
            if "json" not in tipo:
                raise ErroFonte(f"state.json respondeu content-type inesperado: {tipo!r}")
            return json.loads(corpo)
        except urllib.error.HTTPError as exc:
            detalhe = f"HTTP {exc.code}"
            if exc.headers.get("cf-mitigated"):
                detalhe += f" (Cloudflare cf-mitigated={exc.headers.get('cf-mitigated')})"
            erros.append(f"tentativa {tentativa}: {detalhe}")
            if exc.code in (401, 403, 404):
                break  # bloqueio ou ausência: repetir não ajuda
        except (OSError, http.client.HTTPException, ValueError, ErroFonte) as exc:
            # OSError cobre URLError, timeout e conexão encerrada pelo servidor.
            erros.append(f"tentativa {tentativa}: {type(exc).__name__}: {str(exc)[:160]}")
        time.sleep(2 * tentativa)
    raise ErroFonte("state.json indisponível: " + "; ".join(erros))


def ler_sse_estado(prazo: float = 25.0) -> dict:
    """Lê o fluxo SSE apenas até o primeiro evento 'estado' e encerra a conexão."""
    req = urllib.request.Request(URL_SSE, headers={
        "User-Agent": USER_AGENT, "Accept": "text/event-stream"})
    limite = time.monotonic() + prazo
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            evento, dados = "message", []
            while time.monotonic() < limite:
                linha = resp.readline()
                if not linha:
                    break
                linha = linha.decode("utf-8", "replace").rstrip("\r\n")
                if linha.startswith("event:"):
                    evento = linha[6:].strip()
                elif linha.startswith("data:"):
                    dados.append(linha[5:].lstrip())
                elif linha == "":
                    if evento == "estado" and dados:
                        return json.loads("\n".join(dados))
                    evento, dados = "message", []
    except urllib.error.HTTPError as exc:
        raise ErroFonte(f"events respondeu HTTP {exc.code}") from exc
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise ErroFonte(f"events falhou: {type(exc).__name__}: {str(exc)[:160]}") from exc
    raise ErroFonte(f"events não enviou o evento 'estado' em {prazo:.0f} s")


def obter_estado(diagnostico: dict) -> tuple[dict, str, str]:
    try:
        return baixar_state(), "state.json", URL_STATE
    except ErroFonte as exc:
        diagnostico["falhas_acesso"].append(str(exc))
    try:
        return ler_sse_estado(), "sse", URL_SSE
    except ErroFonte as exc:
        diagnostico["falhas_acesso"].append(str(exc))
    raise ErroFonte("nenhuma das duas fontes do Probitas respondeu com dados")


# ---------------------------------------------------------------- validação

def inteiro(valor) -> int | None:
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
        return None
    return valor


def ler_votos(bloco, rotulo: str, problemas: list) -> dict | None:
    if not isinstance(bloco, dict):
        problemas.append(f"{rotulo}: ausente ou não é objeto")
        return None
    votos = {}
    for campo in CAMPOS_VOTOS:
        n = inteiro(bloco.get(campo))
        if n is None:
            problemas.append(f"{rotulo}.{campo}: ausente ou não é inteiro não negativo ({bloco.get(campo)!r})")
            return None
        votos[campo] = n
    return votos


def validar_exterior(estado) -> tuple[dict | None, list, list]:
    """Retorna (dados, problemas, avisos). dados é None se algo essencial falhar."""
    problemas, avisos = [], []
    if not isinstance(estado, dict):
        return None, ["resposta da fonte não é um objeto JSON"], avisos
    ext = estado.get("exterior")
    if not isinstance(ext, dict):
        return None, ["chave 'exterior' ausente: a fonte não publicou dados do exterior"], avisos

    obs = ler_votos(ext.get("obs"), "exterior.obs", problemas)
    sec_rec, sec_tot = inteiro(ext.get("secRec")), inteiro(ext.get("secTot"))
    if sec_rec is None:
        problemas.append(f"exterior.secRec (seções contadas) ausente ou inválido: {ext.get('secRec')!r}")
    if sec_tot is None:
        problemas.append(f"exterior.secTot (total de seções) ausente ou inválido: {ext.get('secTot')!r}")
    cidades = ext.get("cidades")
    if not isinstance(cidades, list) or not cidades:
        problemas.append("exterior.cidades ausente: sem detalhamento por cidade para conferência")
        cidades = []
    if problemas:
        return None, problemas, avisos

    # Contexto eleitoral: exterior e 2º turno.
    if not 1 <= sec_tot <= 5000:
        problemas.append(f"total de seções {sec_tot} incompatível com o exterior (esperado ~1.351)")
    if obs["O"] != 0:
        problemas.append(f"obs.O = {obs['O']} votos em outros candidatos: não é o 2º turno")
    if not 0 <= sec_rec <= sec_tot:
        problemas.append(f"seções contadas ({sec_rec}) fora do intervalo 0..{sec_tot}")
    # Consistência aritmética do boletim de urna.
    soma = obs["L"] + obs["F"] + obs["B"] + obs["N"] + obs["O"]
    if soma != obs["C"]:
        problemas.append(f"comparecimento {obs['C']} difere de L+F+B+N+O = {soma}")
    if obs["C"] > obs["E"]:
        problemas.append(f"comparecimento {obs['C']} maior que eleitores das seções {obs['E']}")

    # Conferência cruzada: a soma das cidades deve reproduzir o total.
    linhas, soma_cid = [], dict.fromkeys(CAMPOS_VOTOS, 0)
    soma_rec = soma_sec = 0
    for i, c in enumerate(cidades):
        p = []
        v = ler_votos(c.get("obs") if isinstance(c, dict) else None, f"cidades[{i}].obs", p)
        rec = inteiro(c.get("rec")) if isinstance(c, dict) else None
        sec = inteiro(c.get("sec")) if isinstance(c, dict) else None
        if p or rec is None or sec is None or rec > sec:
            problemas.append(f"cidade {i} ({c.get('nm') if isinstance(c, dict) else '?'}) com campos inválidos")
            continue
        soma_rec, soma_sec = soma_rec + rec, soma_sec + sec
        for k in CAMPOS_VOTOS:
            soma_cid[k] += v[k]
        if rec > 0:
            linhas.append({
                "code": str(c.get("cd") or ""), "city": str(c.get("nm") or "").strip(),
                "name": str(c.get("pais") or "").strip(), "region": str(c.get("regiao") or "").strip(),
                "lula_votes": v["L"], "flavio_votes": v["F"], "blank_votes": v["B"],
                "null_votes": v["N"], "turnout": v["C"], "electorate": v["E"],
                "sections_counted": rec, "sections_total": sec,
            })
    if soma_rec != sec_rec or soma_sec != sec_tot:
        problemas.append(f"seções por cidade ({soma_rec}/{soma_sec}) não batem com o total ({sec_rec}/{sec_tot})")
    for k in ("L", "F", "B", "N", "C"):
        if soma_cid[k] != obs[k]:
            problemas.append(f"soma das cidades para {k} ({soma_cid[k]}) difere do total ({obs[k]})")
    if problemas:
        return None, problemas, avisos

    if sec_rec == 0 or obs["L"] + obs["F"] == 0:
        return None, ["a fonte ainda não tem seções do exterior contadas no 2º turno"], avisos

    fontes = estado.get("fontes") if isinstance(estado.get("fontes"), dict) else {}
    oficial = ext.get("oficial") if isinstance(ext.get("oficial"), dict) else None
    oficial_tse = None
    if oficial:
        oficial_tse = {
            "lula_votes": inteiro(oficial.get("L")), "flavio_votes": inteiro(oficial.get("F")),
            "sections_counted": inteiro(oficial.get("secApur")),
            "sections_total": inteiro(oficial.get("secTot")),
            "generated_at": oficial.get("dg"), "finished": bool(oficial.get("tf")),
        }
    else:
        avisos.append("exterior.oficial ausente: sem referência da totalização oficial do TSE")
    p_apur = ext.get("pApur")
    linhas.sort(key=lambda x: (-x["sections_counted"], x["name"], x["city"]))
    dados = {
        "lula_votes": obs["L"], "flavio_votes": obs["F"], "blank_votes": obs["B"],
        "null_votes": obs["N"], "turnout": obs["C"], "electorate_counted_sections": obs["E"],
        "sections_counted": sec_rec, "sections_total": sec_tot,
        "electorate_share_counted": round(p_apur, 6) if isinstance(p_apur, (int, float)) else None,
        "source_updated_at": de_epoch_ms(estado.get("at")),
        "last_bulletin_at": de_epoch_ms(fontes.get("bu")),
        "source_version": estado.get("versao"),
        "official_tse": oficial_tse,
        "countries": linhas,
    }
    if dados["source_updated_at"] is None:
        avisos.append("estado.at ausente: horário de atualização da fonte desconhecido")
    return dados, problemas, avisos


def comparar_com_anterior(dados: dict, anterior: dict | None, avisos: list) -> None:
    if not anterior or not anterior.get("validated"):
        return
    for campo, nome in (("sections_counted", "seções contadas"), ("lula_votes", "votos de Lula"),
                        ("flavio_votes", "votos de Flávio")):
        antes = anterior.get(campo)
        if isinstance(antes, int) and dados[campo] < antes:
            avisos.append(f"{nome} diminuíram de {antes} para {dados[campo]} (possível correção na fonte)")


def hash_conteudo(dados: dict) -> str:
    base = {k: dados[k] for k in ("lula_votes", "flavio_votes", "blank_votes", "null_votes",
                                  "turnout", "sections_counted", "sections_total")}
    base["countries"] = [(c["code"], c["lula_votes"], c["flavio_votes"], c["sections_counted"])
                         for c in dados["countries"]]
    return hashlib.sha256(json.dumps(base, sort_keys=True).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- persistência

ESQUEMA = """
CREATE TABLE IF NOT EXISTS leituras (
    coletado_em        TEXT PRIMARY KEY,          -- UTC, ISO 8601
    fonte_atualizada_em TEXT UNIQUE,              -- estado.at da fonte (evita duplicar o mesmo retrato)
    ultimo_boletim_em  TEXT,
    origem             TEXT NOT NULL,
    endpoint           TEXT NOT NULL,
    metodo             TEXT NOT NULL,
    lula_votos         INTEGER NOT NULL CHECK (lula_votos >= 0),
    flavio_votos       INTEGER NOT NULL CHECK (flavio_votos >= 0),
    brancos            INTEGER NOT NULL,
    nulos              INTEGER NOT NULL,
    comparecimento     INTEGER NOT NULL,
    eleitores_secoes_contadas INTEGER NOT NULL,
    secoes_contadas    INTEGER NOT NULL,
    secoes_total       INTEGER NOT NULL,
    hash_conteudo      TEXT NOT NULL,
    CHECK (secoes_contadas BETWEEN 0 AND secoes_total)
);
CREATE TABLE IF NOT EXISTS cidades (
    coletado_em     TEXT NOT NULL REFERENCES leituras(coletado_em) ON DELETE CASCADE,
    codigo          TEXT NOT NULL,
    cidade          TEXT NOT NULL,
    pais            TEXT NOT NULL,
    regiao          TEXT,
    secoes_contadas INTEGER NOT NULL,
    secoes_total    INTEGER NOT NULL,
    lula_votos      INTEGER NOT NULL,
    flavio_votos    INTEGER NOT NULL,
    brancos         INTEGER NOT NULL,
    nulos           INTEGER NOT NULL,
    comparecimento  INTEGER NOT NULL,
    PRIMARY KEY (coletado_em, codigo)
);
"""


def gravar_sqlite(pasta: Path, coletado: dt.datetime, dados: dict, metodo: str,
                  endpoint: str, conteudo: str) -> tuple[Path, bool]:
    """Um banco por hora UTC. Retorna (caminho, inseriu?)."""
    caminho = pasta / f"{coletado.astimezone(dt.timezone.utc):%Y-%m-%d_%H}.sqlite3"
    pasta.mkdir(parents=True, exist_ok=True)
    chave = iso(coletado)
    con = sqlite3.connect(caminho)
    try:
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("PRAGMA foreign_keys=ON")
        con.executescript(ESQUEMA)
        with con:
            cur = con.execute(
                """INSERT OR IGNORE INTO leituras VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (chave, dados["source_updated_at"], dados["last_bulletin_at"], ORIGEM, endpoint,
                 metodo, dados["lula_votes"], dados["flavio_votes"], dados["blank_votes"],
                 dados["null_votes"], dados["turnout"], dados["electorate_counted_sections"],
                 dados["sections_counted"], dados["sections_total"], conteudo))
            inseriu = cur.rowcount == 1
            if inseriu:
                con.executemany(
                    "INSERT INTO cidades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(chave, c["code"], c["city"], c["name"], c["region"], c["sections_counted"],
                      c["sections_total"], c["lula_votes"], c["flavio_votes"], c["blank_votes"],
                      c["null_votes"], c["turnout"]) for c in dados["countries"]])
        if inseriu:
            con.execute("VACUUM")
    finally:
        con.close()
    return caminho, inseriu


def escrever_json(caminho: Path, conteudo: dict) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(caminho.suffix + ".tmp")
    tmp.write_text(json.dumps(conteudo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, caminho)


def resumo_actions(linhas: list[str]) -> None:
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as fh:
            fh.write("\n".join(linhas) + "\n")


# ---------------------------------------------------------------- principal

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dados-dir", default="data", help="pasta de saída (padrão: data)")
    ap.add_argument("--entrada", help="TESTE: lê um state.json local em vez da rede; exige --dados-dir")
    args = ap.parse_args(argv)
    pasta = Path(args.dados_dir)
    if args.entrada and Path(args.dados_dir).resolve() == Path("data").resolve():
        print("Modo teste (--entrada) não pode gravar em data/: use --dados-dir com uma pasta de teste.")
        return 64

    inicio = agora_utc()
    limite = os.environ.get("COLETA_ATE_UTC")
    if limite and not args.entrada:
        fim = dt.datetime.fromisoformat(limite.replace("Z", "+00:00"))
        if inicio > fim:
            print(f"Janela de coleta encerrada em {limite}; nenhum acesso à fonte.")
            resumo_actions([f"Janela de coleta encerrada em {limite}."])
            return 0

    saida, relatorio = pasta / "exterior.json", pasta / "probitas_diagnostico.json"
    anterior = None
    if saida.exists():
        try:
            anterior = json.loads(saida.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            anterior = None

    diag = {"verificado_em": iso(inicio), "origem": ORIGEM, "falhas_acesso": [],
            "metodo": None, "endpoint": None, "problemas": [], "avisos": [], "status": None}
    try:
        if args.entrada:
            estado = json.loads(Path(args.entrada).read_text(encoding="utf-8"))
            metodo, endpoint = "arquivo_local_teste", f"arquivo:{args.entrada}"
        else:
            estado, metodo, endpoint = obter_estado(diag)
        diag["metodo"], diag["endpoint"] = metodo, endpoint
        dados, problemas, avisos = validar_exterior(estado)
    except (ErroFonte, OSError, ValueError) as exc:
        dados, problemas, avisos = None, [str(exc)], []
    diag["problemas"], diag["avisos"] = problemas, avisos

    if dados is None:
        diag["status"] = "sem_dados_validos"
        escrever_json(relatorio, diag)
        print("SEM DADOS VÁLIDOS. O último data/exterior.json foi preservado.")
        for p in diag["falhas_acesso"] + problemas:
            print(f"::error title=Probitas::{p}")
        resumo_actions(["### Coleta Probitas: sem dados válidos", "",
                        *[f"- {p}" for p in diag["falhas_acesso"] + problemas],
                        "", "O último resultado válido foi preservado."])
        return 2

    comparar_com_anterior(dados, anterior, avisos)
    conteudo = hash_conteudo(dados)
    banco, inseriu = gravar_sqlite(pasta / "historico_exterior", inicio, dados, metodo, endpoint, conteudo)
    publicado = {
        "schema": 2,
        "validated": True,
        "source": ORIGEM,
        "source_endpoint": endpoint,
        "extraction_method": metodo,
        "nature": NATUREZA,
        "office": "Presidente", "round": 2, "scope": "exterior",
        "collected_at": iso(inicio),
        **{k: v for k, v in dados.items() if k != "countries"},
        "content_hash": conteudo,
        "changed_since_previous": (anterior or {}).get("content_hash") != conteudo,
        "history_db": banco.as_posix(),
        "warnings": avisos,
        "countries": dados["countries"],
    }
    escrever_json(saida, publicado)
    diag.update(status="validado", leitura_sqlite_inserida=inseriu, banco=banco.as_posix(),
                lula_votes=dados["lula_votes"], flavio_votes=dados["flavio_votes"],
                sections=f"{dados['sections_counted']}/{dados['sections_total']}")
    escrever_json(relatorio, diag)

    linha = (f"Lula {dados['lula_votes']:,} | Flávio {dados['flavio_votes']:,} | "
             f"seções {dados['sections_counted']}/{dados['sections_total']} | "
             f"fonte em {dados['source_updated_at']} | via {metodo}").replace(",", ".")
    print("VALIDADO: " + linha)
    print(f"SQLite: {banco} ({'nova leitura' if inseriu else 'retrato já registrado'})")
    for a in avisos:
        print(f"::warning title=Probitas::{a}")
    resumo_actions(["### Coleta Probitas: validada", "", f"- {linha}",
                    f"- SQLite: `{banco.as_posix()}` ({'nova leitura' if inseriu else 'retrato já registrado'})",
                    *[f"- Aviso: {a}" for a in avisos]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
