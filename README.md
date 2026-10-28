# Arrendar em Lisboa

Corre o scraper de 2 em 2 horas no GitHub Actions, publica o CSV e o site no GitHub Pages
e (opcionalmente) envia email com os anúncios novos.

## Instalação (10 minutos)

1. Crie um repositório no GitHub (pode ser privado, mas o GitHub Pages grátis exige público;
   os anúncios são dados públicos, não há dados pessoais) e envie estes ficheiros.
2. **Settings > Pages**: Source = "Deploy from a branch", Branch = `main`, pasta `/docs`.
3. **Settings > Actions > General > Workflow permissions**: "Read and write permissions".
4. **Actions > Atualizar anúncios > Run workflow** para a primeira execução.
5. O site fica em `https://SEU-UTILIZADOR.github.io/NOME-DO-REPO/`.

## Filtros

**Settings > Secrets and variables > Actions > Variables**, criar `SCRAPER_ARGS`, por exemplo:

    --max-preco 1400 --tipologia 1 2 --paginas 3

## Alertas por email (opcional)

Em **Secrets** criar: `SMTP_USER`, `SMTP_PASS` (palavra-passe de aplicação do Gmail) e `ALERT_EMAIL`.
Sem `ALERT_EMAIL` não é enviado nada.

## Notas

- Se um portal falhar, o log da execução diz qual. Os URLs e padrões estão em `SITES` no script.
- Se todos os portais falharem, o CSV anterior é mantido.
- O GitHub pode atrasar execuções agendadas alguns minutos, e pausa-as se o repositório
  ficar 60 dias sem atividade (o próprio commit automático conta como atividade).
