# Web Scraping — Knowledge

## Knižnice
- `requests` (2.32.5) — HTTP requesty
- `BeautifulSoup4` (bs4) — HTML parsing

## Základný postup
```python
import requests
from bs4 import BeautifulSoup

resp = requests.get(url, timeout=10, headers={"User-Agent": "John-Bot/0.1"})
soup = BeautifulSoup(resp.text, "html.parser")
```

## CSS selektory
- `soup.select(".class")` — podľa triedy
- `soup.select("#id")` — podľa ID
- `soup.select("a[href]")` — atribúty
- `soup.select_one(".class")` — prvý výsledok
- `.get_text(strip=True)` — čistý text
- `.get("href")` — hodnota atribútu

## Stránkovanie
```python
for page in range(1, n+1):
    resp = requests.get(f"{base_url}/page/{page}/", timeout=10)
    # parsuj každú stranu
```

## JSON API
```python
resp = requests.get(api_url, timeout=10)
data = resp.json()
```

## Pravidlá
- Vždy nastaviť `timeout` (10-30s)
- Nastaviť User-Agent
- Rešpektovať robots.txt
- Nespamovať — pauza medzi requestmi (`time.sleep(1)`)
- Kontrolovať `resp.status_code` pred parsovaním

## Otestované na
- quotes.toscrape.com — citáty, stránkovanie, linky
- httpbin.org — headers, User-Agent
- api.github.com — JSON API

## Dátum naučenia
2026-03-24
