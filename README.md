# Eleições 2026 — painel de apuração

Painel responsivo para GitHub Pages com mapa dos estados, votos, percentuais e seções totalizadas a partir dos arquivos oficiais EA20 do TSE.

## Publicação
1. Settings → Pages → Build and deployment: **Deploy from a branch**, **main**, **/(root)**, **Save**.
2. Actions → **Atualizar totalizacao TSE** → **Run workflow** para a primeira coleta.
3. Se a atualização não conseguir fazer commit: Settings → Actions → General → Workflow permissions → **Read and write permissions**.

URL após a publicação: https://felipemirandaeng94.github.io/eleicoes-2026/

## Fontes e limitações
- **Brasil e estados:** arquivos JSON oficiais do TSE, segundo turno presidencial, coletados por GitHub Actions a cada cinco minutos. Agendamentos podem atrasar.
- **Exterior:** link para https://probitasbrasil.org/apuracao/ . **Ainda não há extração automática de boletins individuais do Probitas**; não foi confirmada uma interface estável e autorizada de dados. Não se inventam resultados.
- O navegador procura novo JSON a cada 60 segundos.
- Fonte oficial: https://resultados.tse.jus.br/
- Documentação: https://www.tse.jus.br/eleicoes/informacoes-tecnicas-sobre-a-divulgacao-de-resultados

Painel independente, não oficial. Os dados podem mudar durante a apuração.
