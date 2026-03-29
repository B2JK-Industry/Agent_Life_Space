# GitHub
_Kategória: systems | Tags: github, git, repos | Aktualizované: 2026-03-24_

## Účet
- Organizácia/user: deployment-specific GitHub account
- Token: uložený v vault (GITHUB_TOKEN)

## Repozitáre
- Repozitáre sú deployment-specific a závisia od pripojeného GitHub účtu
- Tento projekt môže pracovať s vlastným repozitárom aj s ďalšími repo, ku ktorým má token prístup

## API
- Base URL: https://api.github.com
- Auth: `Authorization: token $GITHUB_TOKEN`
- Viem: vytvárať repos, issues, PR, reviewovať kód

## Pravidlá
- Push len na main (zatiaľ)
- Jasné commit messages
- Žiadne citlivé dáta v commitoch
