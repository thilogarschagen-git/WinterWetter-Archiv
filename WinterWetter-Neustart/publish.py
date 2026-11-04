"""Collect weather once and export a static, public archive for GitHub Pages."""
import argparse
import html
import json
import os
from pathlib import Path

os.environ['WW_ENABLE_SCHEDULER'] = '0'
import app as weather


def export_site(destination):
    destination.mkdir(parents=True, exist_ok=True)
    now = weather.datetime.now(weather.TZ)
    with weather.con() as db:
        run = db.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 1').fetchone()
        months = {row[0][:7] for row in db.execute('SELECT DISTINCT forecast_hour FROM weather')}
    months.add(now.strftime('%Y-%m'))
    status = dict(run) if run else {'status': 'not_started'}
    status.pop('backup', None)
    errors = json.loads(status.get('errors', '[]'))
    status['errors'] = errors
    (destination / 'status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
    sections = []
    with weather.app.test_client() as client:
        for city, place in weather.PLACES.items():
            links = []
            for month in sorted(months, reverse=True):
                year, number = map(int, month.split('-'))
                response = client.get(f'/report/{city}?year={year}&month={number}')
                if response.status_code != 200 or not response.data.startswith(b'%PDF-'):
                    raise RuntimeError(f'PDF export failed: {city}/{month}')
                filename = f'WinterWetter-{city}-{month}.pdf'
                (destination / filename).write_bytes(response.data)
                links.append(f'<li><a href="{filename}">{month} – PDF herunterladen</a></li>')
            sections.append(f'<section><h2>{html.escape(place["name"])}</h2><p>{html.escape(place["place"])} · vorläufige Koordinaten: {place["lat"]}, {place["lon"]}</p><ul>{"".join(links)}</ul></section>')
    problems = ''.join(f'<li>{html.escape(error)}</li>' for error in errors)
    state = ('<strong>Abruf mit Fehlern</strong><ul>' + problems + '</ul>') if errors else ('Abruf ohne gemeldete Fehler.' if run else 'Noch kein Abruf erfolgt.')
    page = f'''<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WinterWetter Pro – Wetterarchiv</title><style>body{{font:17px system-ui;line-height:1.6;background:#f2f5f9;color:#12243a;max-width:960px;margin:40px auto;padding:0 20px}}section{{background:white;padding:24px;margin:20px 0;border-radius:12px}}a{{color:#125eac}}li{{overflow-wrap:anywhere}}</style>
<h1>❄ WinterWetter Pro</h1><p>Öffentliches Wetterarchiv für Remscheid und Wermelskirchen</p>
<section><h2>Abrufstatus</h2><p>Letzter Versuch: {html.escape(status.get('started_at', '–'))}</p><p>{state}</p><p>Wetterzeilen geprüft: {status.get('weather_count', 0)} · neue Warnversionen: {status.get('warning_count', 0)}</p><p>Geplant: täglich 05:17 und 17:17 UTC (im Winter 06:17/18:17, im Sommer 07:17/19:17 Uhr deutscher Zeit). Ausführungen können sich verzögern.</p></section>
{''.join(sections)}<section><h2>Hinweise zu den Daten</h2><p>Wettermodellwerte von <a href="https://open-meteo.com/">Open-Meteo</a> (<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>), keine Vor-Ort-Messungen. Warnungsquelle: <a href="https://www.dwd.de/">Deutscher Wetterdienst</a>. Koordinaten sind vorläufig. Fehlende archivierte Warnungen belegen keine Warnungsfreiheit. Zwei Abrufe pro Tag können kurzlebige Warnungen verpassen. Diese Entwicklungsversion ist kein rechtsverbindlicher Nachweis für Winterdiensteinsätze.</p><p>Die kostenlose Open-Meteo-API ist für nichtkommerzielle Nutzung vorgesehen. Gewerbliche Nutzung muss vor dem Einsatz anhand der aktuellen Anbieterbedingungen geklärt werden.</p></section></html>'''
    (destination / 'index.html').write_text(page, encoding='utf-8')
    (destination / '.nojekyll').touch()
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--skip-collect', action='store_true')
    args = parser.parse_args()
    if not args.skip_collect:
        result = weather.collect_all()
        if result.get('error'):
            raise RuntimeError(result['error'])
        print(json.dumps(result, ensure_ascii=False))
    export_site(args.output)


if __name__ == '__main__':
    main()
