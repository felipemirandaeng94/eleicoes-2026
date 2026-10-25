# Eleições 2026 — painel de apuração

Painel responsivo para GitHub Pages com mapa dos estados, votos, percentuais e seções totalizadas a partir dos arquivos oficiais EA20 do TSE.

## Publicação
1. Settings → Pages → Build and deployment: **Deploy from a branch**, **main**, **/(root)**, **Save**.
2. Actions → **Atualizar totalizacao TSE** → **Run workflow** para a primeira coleta.
3. Se a atualização não conseguir fazer commit: Settings → Actions → General → Workflow permissions → **Read and write permissions**.

URL após a publicação: https://felipemirandaeng94.github.io/eleicoes-2026/

## Fontes e limitações
- **Brasil e estados:** arquivos JSON oficiais do TSE, segundo turno presidencial, coletados por GitHub Actions a cada cinco minutos. Agendamentos podem atrasar.
- **Exterior:** contagem **não oficial** do [Probitas Brasil](https://probitasbrasil.org/apuracao/) (soma dos boletins de urna do exterior já publicados pelo TSE). O workflow **Coletar Probitas Exterior** (`scripts/coletar_probitas.py`) lê a cada cinco minutos `https://probitasbrasil.org/apuracao/state.json` — o mesmo retrato JSON que a página do Probitas usa — com o fluxo `/apuracao/events` como reserva. Usa apenas `exterior.obs` (votos do 2º turno) e `secRec`/`secTot` (seções); campos de 1º turno, projeção e Brasil são ignorados. Cada leitura validada vai para `data/historico_exterior/AAAA-MM-DD_HH.sqlite3` (hora UTC; tabelas `leituras` e `cidades`) e para `data/exterior.json`. Se a coleta falhar, o último resultado válido é mantido e o painel o marca como desatualizado após 20 minutos. Não se inventam resultados.
- Conferir o histórico: `sqlite3 data/historico_exterior/2026-10-25_14.sqlite3 "SELECT coletado_em, lula_votos, flavio_votos, secoes_contadas, secoes_total FROM leituras"`.
- O navegador procura novo JSON a cada 60 segundos.
- Fonte oficial: https://resultados.tse.jus.br/
- Documentação: https://www.tse.jus.br/eleicoes/informacoes-tecnicas-sobre-a-divulgacao-de-resultados

Painel independente, não oficial. Os dados podem mudar durante a apuração.
